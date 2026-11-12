from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import UploadFile
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.exceptions import CustomException
from app.plugin.module_ecommerce.common.import_batch.model import (
    BATCH_STATUS_CONFIRMED,
    FILE_CATEGORY_BILL,
    FILE_CATEGORY_ORDER,
    ImportBatchModel,
    build_batch_no,
)
from app.plugin.module_ecommerce.common.import_batch.schema import BatchConfirmResultSchema, BillBatchCountSchema, BillGateGapSchema
from app.plugin.module_ecommerce.common.import_batch.service import (
    EMPTY_IMPORT_FILE_MSG,
    ImportBatchService,
    validate_month,
)
from app.plugin.module_ecommerce.common.numbers import MONEY_QUANT, WEIGHT_QUANT, quantize_half_up, round_cent
from app.plugin.module_ecommerce.order.crud import OrderCRUD, OrderDetailCRUD
from app.plugin.module_ecommerce.order.model import OrderDetailModel, OrderModel
from app.utils.common_util import search_to_dict

from .crud import BillParseRuleCRUD, CourierBillCRUD
from .model import (
    BLOCKING_STATUSES,
    ORIGINAL_STATUSES,
    STATUS_DUPLICATE,
    STATUS_FAKE,
    STATUS_MATCHED,
    STATUS_RESTORED,
    STATUS_WEIGHT_PROVINCE,
    BillParseRuleModel,
    CourierBillModel,
)
from .parsers.base import NonBillRow, RawBillRow
from .parsers.generic_parser import get_bill_parser, normalize_province
from .schema import (
    BillImportResultSchema,
    BillParseRuleCreateSchema,
    BillParseRuleOutSchema,
    BillParseRuleQueryParam,
    BillParseRuleUpdateSchema,
    CourierBillOutSchema,
    CourierBillQueryParam,
)

# 重量异常容差：固定 0.05kg + 理论重量 5%（只判超重，轻了不算异常）
WEIGHT_TOLERANCE_FIXED = Decimal("0.05")
WEIGHT_TOLERANCE_RATE = Decimal("0.05")


class BillParseRuleService:
    """账单解析规则服务层（ADR-0029）"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        self.auth = auth
        self.db = db

    async def detail(self, id: int) -> BillParseRuleOutSchema:
        obj = await BillParseRuleCRUD(self.auth, self.db).get(id=id)
        if not obj:
            raise CustomException(msg="该账单解析规则不存在")
        return BillParseRuleOutSchema.model_validate(obj)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: BillParseRuleQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[BillParseRuleOutSchema]:
        offset = (page_no - 1) * page_size
        return await BillParseRuleCRUD(self.auth, self.db).page(
            offset=offset,
            limit=page_size,
            order_by=order_by or [{"id": "asc"}],
            search=search_to_dict(search, {}),
            out_schema=BillParseRuleOutSchema,
        )

    async def create(self, data: BillParseRuleCreateSchema) -> BillParseRuleOutSchema:
        exist_obj = await BillParseRuleCRUD(self.auth, self.db).get(bill_type=data.bill_type)
        if exist_obj:
            raise CustomException(msg="创建失败，该账单类型编码已存在解析规则")
        obj = await BillParseRuleCRUD(self.auth, self.db).create(data=data)
        return BillParseRuleOutSchema.model_validate(obj)

    async def update(self, id: int, data: BillParseRuleUpdateSchema) -> BillParseRuleOutSchema:
        obj = await BillParseRuleCRUD(self.auth, self.db).get(id=id)
        if not obj:
            raise CustomException(msg="更新失败，该账单解析规则不存在")
        obj = await BillParseRuleCRUD(self.auth, self.db).update(id=id, data=data)
        return BillParseRuleOutSchema.model_validate(obj)

    async def delete(self, ids: list[int]) -> None:
        if not ids:
            raise CustomException(msg="删除失败，删除对象不能为空")
        objs: Sequence = await BillParseRuleCRUD(self.auth, self.db).get_list(search={"id": ("in", ids)})
        obj_map = {o.id: o for o in objs}
        for id_ in ids:
            if id_ not in obj_map:
                raise CustomException(msg="删除失败，该账单解析规则不存在")
        # 物理删除，且不设「已导过账单就拒绝」的前提（§15.5 / ADR-0029）：bill_type 上是真唯一索引，
        # 软删的幽灵行会永久占住这个类型名，同类型再也建不出规则。删掉只少一份配置，不动任何已入库的钱。
        await self.db.execute(delete(BillParseRuleModel).where(BillParseRuleModel.id.in_(ids)))
        await self.db.flush()


@dataclass
class _WaybillGroup:
    """一个运单号在摊分时的完整视图：原件账单行 + 要挂的子订单。"""

    waybill_no: str
    bill_rows: list[CourierBillModel] = field(default_factory=list)
    details: list[OrderDetailModel] = field(default_factory=list)

    @property
    def total_amount(self) -> Decimal:
        return sum((r.actual_freight for r in self.bill_rows), Decimal("0"))

    @property
    def weight_keys(self) -> list[Decimal]:
        return [(d.weight_kg or Decimal("0")) * (d.ship_qty or 0) for d in self.details]

    @property
    def qty_keys(self) -> list[int]:
        return [d.ship_qty or 0 for d in self.details]


def _apportion(total: Decimal, keys: list[Decimal]) -> list[Decimal]:
    """按权重把整票金额摊到各子订单；末位余数补给权重最大的一项，保证求和恰好等于整票金额。

    单份份额四舍五入到分（`round_cent`，方向写死——原先用默认 `.quantize(0.01)` 是凑偶，
    §21.3 AG4）。**方向不破坏「Σ份额 = 整票金额」**：逐条舍完之后余数照旧补给权重最大的那条，
    ADR-0034 那条不变式与方向无关；方向只决定「同一条子订单拿到哪一分」。
    """
    weight_sum = sum(keys, Decimal("0"))
    if weight_sum <= 0:
        return [Decimal("0") for _ in keys]
    shares = [total * w / weight_sum for w in keys]
    shares = [round_cent(s) for s in shares]
    residual = round_cent(total) - sum(shares, Decimal("0"))
    if residual != 0:
        shares[max(range(len(keys)), key=lambda i: keys[i])] += residual
    return shares


# ── 认领订单写进子订单「更新备注」的标记（§22.4）────────────────────────────
# 这段文字**只写不读**（§23，AJ 轮）：「重新订正」删掉之后它不再是撤销凭据，没有任何代码解析它，
# 所以文案改版、或有人在库里手工编辑过这条备注，都不会伤到功能——AI 轮那条技术债随撤销一起消失。

_CLAIM_MARKER_HEAD = "账单订正写入快递单号"


def _fmt_remark_value(value: str | None) -> str:
    """备注里的值展示：None 与空串都显示「空」（与订单更新那侧同一个写法）。"""
    return "空" if value is None or value == "" else value


def _claim_marker_key(row: CourierBillModel) -> str:
    """本条账单行的标记前缀，精确到「哪一票、哪个批次、哪一行」。

    精确到账单行 id 是因为同一个运单号可以有两行虚假账单（判定虚假优先于重复），各自认领各的，
    备注里要分得清这一格是谁写进去的。
    """
    return f"{_CLAIM_MARKER_HEAD} {row.waybill_no}（批次 {row.import_batch_id} 行ID {row.id}）原值："


def _claim_marker(row: CourierBillModel, old_value: str | None, ts: str) -> str:
    return f"[{ts}] {_claim_marker_key(row)}{_fmt_remark_value(old_value)}；"


class LogisticsService:
    """快递账单服务层（§5.2）：导入判定 → 筛异常处理 → 批次确认回写（ADR-0034）"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        self.auth = auth
        self.db = db
        self.batch_service = ImportBatchService(auth, db)

    @property
    def _bill_crud(self) -> CourierBillCRUD:
        return CourierBillCRUD(self.auth, self.db)

    # ── 查询 ────────────────────────────────────────────────────────────

    async def courier_bill_page(
        self,
        page_no: int,
        page_size: int,
        search: CourierBillQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[CourierBillOutSchema]:
        """账单行分页；系统订单号与子订单数按运单号现查现拼，不额外落库（匹配表已删）。"""
        offset = (page_no - 1) * page_size
        result: PageResultSchema[CourierBillOutSchema] = await self._bill_crud.page(
            offset=offset,
            limit=page_size,
            order_by=order_by or [{"id": "asc"}],
            search=search_to_dict(search, {}),
            preload=["store"],
            out_schema=CourierBillOutSchema,
        )
        waybills = {item.waybill_no for item in result.items}
        grouped = await self._load_shipped_details_by_waybill(waybills)
        for item in result.items:
            details = grouped.get(item.waybill_no, [])
            item.detail_count = len(details)
            item.system_order_nos = "、".join(dict.fromkeys(d.order.system_order_no for d in details if d.order))
        return result

    async def bill_batch_counts(self, batch_no: str) -> BillBatchCountSchema:
        """批次详情顶部的一行计数（正常/重复/虚假/重量省份/已恢复 + 发货月分布）。

        发货月分布用来回答「这份账单到底属于哪个月」：一票取它命中子订单里最早的发货月
        （与订单归属月同一条口径），挂不上订单的票不进分布。打开详情时现算，不落库。

        先认批次类别：这个接口按 `import_batch_id` 查账单表，塞一个订单批次号进来查不到任何行，
        会回一串 0 让界面以为「这个批次是空的」（§16 A6）。
        """
        batch = await self.batch_service.get_by_batch_no(batch_no)
        if not batch:
            raise CustomException(msg=f"批次 {batch_no} 不存在")
        if batch.file_category != FILE_CATEGORY_BILL:
            raise CustomException(msg=f"批次 {batch_no} 不是账单批次，没有账单行可计数")
        rows: Sequence[CourierBillModel] = await self._bill_crud.get_list(
            search={"import_batch_id": ("eq", batch_no)},
            load_columns=[CourierBillModel.status, CourierBillModel.waybill_no],
        )
        counter = Counter(r.status for r in rows)
        grouped = await self._load_shipped_details_by_waybill({r.waybill_no for r in rows})
        distribution: Counter[str] = Counter()
        for details in grouped.values():
            months = [d.ship_time.strftime("%Y-%m") for d in details if d.ship_time]
            if months:
                distribution[min(months)] += 1
        return BillBatchCountSchema(
            total=len(rows),
            matched=counter[STATUS_MATCHED],
            duplicate=counter[STATUS_DUPLICATE],
            fake=counter[STATUS_FAKE],
            weight_province=counter[STATUS_WEIGHT_PROVINCE],
            restored=counter[STATUS_RESTORED],
            ship_month_distribution=dict(sorted(distribution.items())),
        )

    # ── 导入 ────────────────────────────────────────────────────────────

    async def import_bill(self, *, file: UploadFile, bill_type: str, bill_month: str) -> BillImportResultSchema:
        """导入快递账单：落库 + 当场判定，确认动作留给单独端点（三步式，ADR-0034）。

        1. 出账月份只校验格式与「不是未来月」；可导入月份窗口（本月往前 3 个整月）是前端护栏，
           同月同快递已有批次即拒绝
        2. Excel 只解析一遍；「非账单行」（账单日期读不出，含快递合计行）先摘掉、不入库，
           批次行数只数留下的账单行；行账单日期必须落在所选月内，否则整批不落库
        3. 两趟预取（按运单号查 ERP发货 且非分销 的子订单、查既有原件行）后逐行判定，避免逐条查库
        4. 判定优先级 虚假 > 重复 > 重量省份；原件行才带 actual_freight
        5. 判定完成后把对账结果合成一行写进批次备注（ADR-0037）；合计行对不上**不拒绝入库**，
           只在导入结果里提示一句

        空文件在 `create_batch` 之后才解析得到，但请求级事务会随异常回滚，批次行不会留下。
        """
        bill_month = validate_month(bill_month, field_name="出账月份")

        rule = await BillParseRuleCRUD(self.auth, self.db).get(bill_type=bill_type)
        if not rule:
            raise CustomException(msg=f"未配置账单类型 {bill_type} 的解析规则，请先在「解析规则管理」中新增")
        # 停用 = 这个月不该再有这家账单。「门禁预检」和「确认批次」早就只认启用规则，这里补齐另一半，
        # 否则同一个「停用」在四道程序里两种理解：能导进来、却确认不了。
        if rule.status != 0:
            raise CustomException(msg=f"账单类型 {bill_type} 的解析规则已停用，请先在「解析规则管理」中启用再导入")

        batch = await self.batch_service.create_batch(
            file_category=FILE_CATEGORY_BILL, batch_month=bill_month, sub_category=bill_type,
        )

        contents = await file.read()
        await file.close()
        if not contents:
            raise CustomException(msg=EMPTY_IMPORT_FILE_MSG)

        parser = get_bill_parser(bill_type)
        rows: list[RawBillRow] = await parser.aparse(contents, rule.column_mapping)
        if not rows:
            raise CustomException(msg=EMPTY_IMPORT_FILE_MSG)
        # 「非账单行」已被解析器摘掉：批次行数只数真正入库的账单行，少了哪几行、少的是不是真钱，
        # 全写进下面那行批次备注（ADR-0037）
        non_bill_rows: list[NonBillRow] = list(parser.non_bill_rows)

        self._assert_dates_in_month(rows, bill_month=bill_month)

        grouped = await self._load_shipped_details_by_waybill({row.waybill_no for row in rows})
        # 判重基线：只比"原件行"，已软删的行 get_list 默认排除，虚假行本来就不算历史
        existing_originals: Sequence[CourierBillModel] = await self._bill_crud.get_list(
            search={"waybill_no": ("in", sorted({row.waybill_no for row in rows})), "status": ("in", list(ORIGINAL_STATUSES))},
        )

        originals = self._judge_rows(
            rows=rows, bill_month=bill_month, batch_no=batch.batch_no, bill_type=bill_type,
            waybill_fee=rule.waybill_fee, grouped=grouped, existing_originals=existing_originals,
        )
        await self._bill_crud.bulk_create(originals)
        await self.batch_service.add_counts(batch, row_count=len(rows), order_count=len({row.waybill_no for row in rows}))

        counter = Counter(row["status"] for row in originals)
        fake, duplicate = counter[STATUS_FAKE], counter[STATUS_DUPLICATE]
        message = (
            f"批次 {batch.batch_no} 导入 {len(rows)} 行：已匹配 {counter[STATUS_MATCHED]}、"
            f"重量省份异常 {counter[STATUS_WEIGHT_PROVINCE]}、重复 {duplicate}、虚假 {fake}"
        )
        if fake or duplicate:
            message += f"；待处理异常 {fake + duplicate} 行，处理完才能确认批次"
        reconciliation, gap_warning = self._reconcile_bill(
            rows, non_bill_rows, waybill_fee=rule.waybill_fee, counter=counter,
        )
        await self.batch_service.append_remark(batch, reconciliation)
        if gap_warning:
            # 对不上不拒绝入库（ADR-0037：账单是快递给的唯一事实来源，挡在门外连异常行都没法处理），
            # 但这句话必须出现在导入结果里，不能只躺在备注等人去翻
            message += f"；{gap_warning}"
        return BillImportResultSchema(
            batch_no=batch.batch_no,
            row_count=len(rows),
            matched=counter[STATUS_MATCHED],
            weight_province=counter[STATUS_WEIGHT_PROVINCE],
            duplicate=duplicate,
            fake=fake,
            message=message,
        )

    @staticmethod
    def _reconcile_bill(
        rows: list[RawBillRow],
        non_bill_rows: list[NonBillRow],
        *,
        waybill_fee: Decimal,
        counter: Counter[str],
    ) -> tuple[str, str | None]:
        """把「合计行三项」与匹配结果合成一行批次备注（ADR-0037，2026-10-28 决议 AC12=乙 + AC14）。

        返回 ``(备注文本, 需要当面提示的异常一句话或 None)``。

        - **件量核不了**：五项列映射里没有件量列，只有重量与费用两把尺子可比，所以备注只报
          「件量 = 账单行数」这个我们自己数的数，不谎称对过快递的件量。
        - **面单费不在快递的合计行里**（那是本公司按快递公司配置的固定单价），所以反推整票
          运费 = Σ其它费用 + 面单费 × 件量，把反推数一起写出来让人一眼能核。
        - 非账单行逐行报「行号 + 重量 + 费用」，让人看出少了哪几行、少的是不是真钱。
        """
        def _num(value: Decimal | None, quant: Decimal) -> str:
            # 展示也走同一把尺（默认 `.quantize` 是凑偶，§21.3 AG4）。备注里报出来的数必须
            # 与库里那些行加起来是同一个数，人才能逐行核（ADR-0039）。
            return "-" if value is None else str(quantize_half_up(value, quant))

        sum_weight = sum((r.bill_weight or Decimal("0") for r in rows), Decimal("0"))
        sum_fee = sum((r.bill_other_fee for r in rows), Decimal("0"))
        parts = [
            f"对账：账单行 {len(rows)} 票（件量），Σ账单重量 {_num(sum_weight, WEIGHT_QUANT)}kg、"
            f"Σ其它费用 {_num(sum_fee, MONEY_QUANT)}元",
            f"反推整票运费合计 {_num(sum_fee + waybill_fee * len(rows), MONEY_QUANT)}元"
            f"（= Σ其它费用 + 面单费 {_num(waybill_fee, MONEY_QUANT)} × 件量）",
        ]
        warning: str | None = None

        if non_bill_rows:
            parts.append(
                "非账单行 "
                + str(len(non_bill_rows))
                + " 行："
                + "、".join(
                    f"第{r.row_no}行(重量 {_num(r.bill_weight, WEIGHT_QUANT)}、费用 {_num(r.bill_other_fee, MONEY_QUANT)})"
                    for r in non_bill_rows
                )
            )
            # 只有真带数字的非账单行（合计行）才是核对基准；空白分隔行一个数字都没有，
            # 拿 0 去比 Σ 会报出一条假的「对不上」
            carriers = [r for r in non_bill_rows if r.bill_weight is not None or r.bill_other_fee is not None]
            if carriers:
                claim_weight = sum((r.bill_weight or Decimal("0") for r in carriers), Decimal("0"))
                claim_fee = sum((r.bill_other_fee or Decimal("0") for r in carriers), Decimal("0"))
                if claim_weight == sum_weight and claim_fee == sum_fee:
                    parts.append(f"合计行与逐行求和一致（重量 {_num(claim_weight, WEIGHT_QUANT)}、费用 {_num(claim_fee, MONEY_QUANT)}）")
                else:
                    parts.append(
                        f"合计行与逐行求和不一致：重量差 {_num(claim_weight - sum_weight, WEIGHT_QUANT)}kg、"
                        f"费用差 {_num(claim_fee - sum_fee, MONEY_QUANT)}元"
                    )
                    warning = "快递合计行与逐行求和对不上（差额见批次备注），本批照样已入库，请核对后再确认批次"
            else:
                parts.append("非账单行没带重量/费用数字，本次没有外部核对基准")
        else:
            parts.append("本次没读到非账单行（快递合计行），重量与费用没有外部核对基准")

        parts.append(
            f"判定：已匹配 {counter[STATUS_MATCHED]}、重量省份异常 {counter[STATUS_WEIGHT_PROVINCE]}、"
            f"重复 {counter[STATUS_DUPLICATE]}、虚假 {counter[STATUS_FAKE]}"
        )
        return "；".join(parts) + "。", warning

    @staticmethod
    def _assert_dates_in_month(rows: list[RawBillRow], *, bill_month: str) -> None:
        """行账单日期必须落在所选出账月内。"""
        bad = [f"第{row.row_no}行({row.bill_date})" for row in rows if row.bill_date.strftime("%Y-%m") != bill_month]
        if bad:
            raise CustomException(
                msg=f"账单日期与所选出账月 {bill_month} 不符，整批不落库：{'、'.join(bad[:20])}"
                + (f" 等 {len(bad)} 行" if len(bad) > 20 else ""),
            )

    def _judge_rows(
        self,
        *,
        rows: list[RawBillRow],
        bill_month: str,
        batch_no: str,
        bill_type: str,
        waybill_fee: Decimal,
        grouped: dict[str, list[OrderDetailModel]],
        existing_originals: Sequence[CourierBillModel],
    ) -> list[dict[str, Any]]:
        """逐行判定状态与金额（纯内存，不落库）。

        虚假 > 重复 > 重量省份：两条查不到子订单的行都判虚假、不判重复；
        同一批次同一运单号按行序第一条当原件。
        """
        claimed: dict[str, str] = {b.waybill_no: f"已有批次 {b.import_batch_id} 的账单行 {b.id}" for b in existing_originals}
        result: list[dict[str, Any]] = []

        for row in rows:
            # 行号用解析器给的文件行序号，非账单行占的那个号不补位（ADR-0037）
            index = row.row_no
            details = sorted(grouped.get(row.waybill_no, []), key=_detail_order_key)
            reason: str | None = None
            status = STATUS_MATCHED

            if not details:
                status = STATUS_FAKE
                # 只说查了什么、没查到什么（§20.6 AF8）：分销行在取候选子订单那一步就被过滤掉了，
                # 走不到这里，所以这一格**推不出**「这单是分销」。备注是业务方拿去做判断的依据，
                # 写一句推不出来的话等于给他们假线索。
                reason = "无对应ERP发货子订单（匹配只看非分销的ERP发货子订单）"
            elif (holder := claimed.get(row.waybill_no)) is not None:
                status = STATUS_DUPLICATE
                reason = f"与{holder}重复"
            else:
                claimed[row.waybill_no] = f"本批次第{index}行"
                reasons: list[str] = []
                store_ids = {d.order.store_id for d in details if d.order}
                if len(store_ids) > 1:
                    reasons.append("跨店铺")
                anomaly = _weight_province_reason(row, details)
                if anomaly:
                    reasons.append(anomaly)
                    status = STATUS_WEIGHT_PROVINCE
                reason = "；".join(reasons) or None

            original = status in ORIGINAL_STATUSES
            result.append({
                "import_batch_id": batch_no,
                "bill_month": bill_month,
                "bill_date": row.bill_date,
                "bill_type": bill_type,
                "waybill_no": row.waybill_no,
                "bill_weight": row.bill_weight,
                "receiver_province": row.receiver_province,
                "waybill_fee": waybill_fee,
                "bill_other_fee": row.bill_other_fee,
                "actual_freight": (waybill_fee + row.bill_other_fee) if original else Decimal("0"),
                "store_id": details[0].order.store_id if original and details and details[0].order else None,
                "status": status,
                "anomaly_remark": f"第{index}行：{reason}" if reason else None,
            })

        return result

    async def _load_shipped_details_by_waybill(self, waybills: set[str]) -> dict[str, list[OrderDetailModel]]:
        """全局按运单号取「ERP发货 且非分销」的子订单（不看发货月、不回溯上月，ADR-0034）。

        分销订单从这条线上摘干净（2026-10-28 决议 AC26=乙，ADR-0036）：那批行的单号是商家侧
        承运商的，永远进不了我们的账单，留着只会让 `freight_cost` 常年 NULL；更坏的是号一旦撞上
        （1688 商家用的可能正是我们月结那家），代发单会被当原件行参加摊分——一笔我们根本没付的
        运费进成本，还抢走同票真发货那几条的份额。
        """
        if not waybills:
            return {}
        details: Sequence[OrderDetailModel] = await OrderDetailCRUD(self.auth, self.db).get_list(
            search={
                "tracking_no": ("in", sorted(waybills)),
                "is_erp_shipped": ("eq", True),
                "distribution_order_type": ("eq", 0),
            },
            preload=["order"],
        )
        grouped: dict[str, list[OrderDetailModel]] = defaultdict(list)
        for d in details:
            if d.tracking_no:
                grouped[d.tracking_no].append(d)
        return grouped

    # ── 异常处理 ────────────────────────────────────────────────────────

    async def delete_bill_rows(self, ids: list[int]) -> str:
        """逻辑删除：只允许删未确认批次里的虚假/重复行（重量省份异常不许删）。

        删到该批次名下没有未软删的行时，**批次自己跟着一起物理删除**（§22.6 / ADR-0033）：
        留着一条零行批次，门禁会一直问「这个月这家快递还没确认」，而界面上又没有任何账单可确认。
        软删行也一并删掉——`batch_no` 的唯一索引不认 `is_deleted`，不删干净同月同快递重导必撞键。
        """
        rows = await self._require_status_in(
            ids, allowed=BLOCKING_STATUSES, allowed_label="虚假/重复", action="删除或改对",
        )
        await self._bill_crud.delete(ids=[r.id for r in rows])
        per_batch = Counter(r.import_batch_id for r in rows if r.import_batch_id)
        parts = [f"已删除 {len(rows)} 行"]
        for batch_no, deleted in sorted(per_batch.items()):
            batch = await self.batch_service.get_by_batch_no(batch_no)
            if batch is None:
                # 挂不到批次的行（脏数据报警，ADR-0033）：没有批次可减计数、也没有批次可删
                continue
            if await self._bill_crud.count(import_batch_id=("eq", batch_no)):
                # 下拉那句「N 行 / M 票」只有这一列做数据源，不减就成了「下拉说 500、点进去 497」
                batch.row_count = max(batch.row_count - deleted, 0)
                await self.db.flush()
                continue
            await self._discard_batch(batch)
            parts.append(f"批次 {batch_no} 已无账单行，连批次和已删除的行一并物理删除")
        return "；".join(parts)

    async def restore_bill_rows(
        self,
        ids: list[int],
        remark: str | None = None,
        *,
        system_order_no: str | None = None,
        detail_ids: list[int] | None = None,
    ) -> int:
        """人工把虚假/重复行改对后置「已恢复」：钱开始计入成本，随批次确认参与摊分。

        **虚假行必须认领订单**（§22.1）：它的运单号本来就查不到子订单，光翻状态的话确认批次时
        这一票的钱无处安放、整批确认会被 `_write_freight` 拒掉。认领＝把本票运单号订正进勾选的
        子订单行，摊分那一条线一个字不改。

        **已恢复是终点**（§23，AJ 轮）：一行只要被人工判定过，后端就不再接受任何改动——换勾选的
        子订单行、改回虚假/重复、删除都不行，勾错了只能直接修改数据库。
        """
        order_no = (system_order_no or "").strip()
        picked = sorted(set(detail_ids or []))
        if bool(order_no) != bool(picked):
            raise CustomException(msg="认领失败：系统订单号和勾选的子订单行要一起给，只给一半不动任何数据")
        if order_no:
            return await self._claim_and_restore(ids, order_no, picked, remark)

        rows = await self._require_status_in(
            ids, allowed=BLOCKING_STATUSES, allowed_label="虚假/重复", action="删除或改对",
        )
        fake = sorted(r.id for r in rows if r.status == STATUS_FAKE)
        if fake:
            raise CustomException(
                msg=f"以下虚假行还没认领订单，不能直接改对（改对之后这一票摊不下去，整个批次确认会被拒）：{fake[:20]}"
            )
        for row in rows:
            self._mark_restored(row, f"；人工改对：{remark}" if remark else "；人工改对")
        await self.db.flush()
        return len(rows)

    async def _claim_and_restore(
        self, ids: list[int], order_no: str, detail_ids: list[int], remark: str | None,
    ) -> int:
        """认领一票：四道闸全过再写运单号，最后翻状态存金额。写完就是终点（§23）。"""
        if len(ids) != 1:
            raise CustomException(msg="认领失败：一次只能给一票认领（一条账单行 + 一个系统订单号 + 勾选的子订单行）")
        # 已恢复与重复行都先放进这道闸，好让各自那句点名报错说清真实原因；
        # 而批次已确认的行让「批次已确认，一律不动」这条更硬的理由先赢（§22.5）。
        row = (await self._require_status_in(
            ids, allowed=(*BLOCKING_STATUSES, STATUS_RESTORED), allowed_label="虚假", action="认领改对",
        ))[0]
        if row.status == STATUS_DUPLICATE:
            raise CustomException(
                msg=f"认领失败：运单 {row.waybill_no} 是重复行，它的单号本来就查得到子订单，改对只需翻状态、不用填系统订单号"
            )
        if row.status == STATUS_RESTORED:
            raise CustomException(
                msg=f"认领失败：运单 {row.waybill_no} 已经是「已恢复」，人工判定过就是终点（§23）——"
                    "不能换勾选的子订单行，也改不回虚假；要撤销这一票只能直接修改数据库"
            )
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        details = await self._check_claim_gates(row, order_no, detail_ids)
        carrier = await self._carrier_name(row.bill_type)
        for detail in details:
            detail.update_remark = f"{detail.update_remark or ''}{_claim_marker(row, detail.tracking_no, ts)}"
            detail.tracking_no = row.waybill_no
        suffix = f"；认领改对：{remark}" if remark else "；认领改对"
        self._mark_restored(row, f"{suffix}（认领订单 {order_no}，承运商 {carrier}）")
        await self.db.flush()
        return 1

    def _mark_restored(self, row: CourierBillModel, suffix: str) -> None:
        row.status = STATUS_RESTORED
        row.actual_freight = row.waybill_fee + row.bill_other_fee
        row.anomaly_remark = f"{row.anomaly_remark or ''}{suffix}"

    async def _check_claim_gates(
        self, row: CourierBillModel, order_no: str, detail_ids: list[int],
    ) -> list[OrderDetailModel]:
        """认领的四道闸（§22.3 ＋ §26.5；§26.6 把「目标订单批次已确认」那道整条删除）：写之前全部判完，任一不过就整笔报错，不留半笔。"""
        # §26.5（乙）：一票只许有一张原件行进摊分组。下面 `old_waybills` 那道闸管的是**反方向**
        # （勾选行的旧单号被别人占着），拦不住这里——第一次认领刚把勾选行的 `tracking_no` 写成
        # 本行的 `waybill_no`，正好被那句 `!= row.waybill_no` 排除，于是同号的第二张虚假行一路绿灯，
        # 两张原件行进同一组、`total_amount` 变成两份钱，1.90 的票摊成 3.80 直接进利润。
        # 这道闸只看本行自己的单号，与勾了哪些行无关，所以排在最前面先讲清「这票有主人了」。
        prior: Sequence[CourierBillModel] = await self._bill_crud.get_list(
            search={"waybill_no": ("eq", row.waybill_no), "status": ("in", list(ORIGINAL_STATUSES))},
        )
        holders = [p for p in prior if p.id != row.id]
        if holders:
            named = "、".join(f"账单行 {p.id}（批次 {p.import_batch_id}）" for p in holders[:5])
            raise CustomException(
                msg=f"认领失败：运单 {row.waybill_no} 这一票已经有原件行在挂着了——{named}。"
                    "再认领一次就是把同一份运费算两遍；本行是同号的第二张，要处理请删除它"
            )
        orders: Sequence[OrderModel] = await OrderCRUD(self.auth, self.db).get_list(
            search={"system_order_no": ("eq", order_no)},
        )
        if not orders:
            raise CustomException(msg=f"认领失败：系统订单号 {order_no} 在订单表里查不到")
        order = orders[0]
        # §26.6：窗口锚在**出账月**，往前只放一档。快递公司的节奏是「9 月底发的货进 10 月的账单，
        # 10 月的账单 11 月才拿到」——只许同月等于把这批真金白银付出去的运费永远摊不进成本。
        # 比的是账单行自己的 `bill_month` 与目标订单的批次号，不是「今天几号」：操作员哪天点导入
        # 都不该改变闸门开合。再往前一档仍然拒（滞后两个月出账不正常，得先核对是不是这一家的票）。
        allowed_batches = [
            build_batch_no(FILE_CATEGORY_ORDER, row.bill_month),
            build_batch_no(FILE_CATEGORY_ORDER, _prev_month(row.bill_month)),
        ]
        if order.import_batch_id not in allowed_batches:
            raise CustomException(
                msg=f"认领失败：{order_no} 属于订单批次 {order.import_batch_id}，本票出账月 {row.bill_month}，"
                    f"只许挂 {' 或 '.join(allowed_batches)}（上月末发货、本月才出账的那种），再早的月不接"
            )
        details: Sequence[OrderDetailModel] = await OrderDetailCRUD(self.auth, self.db).get_list(
            search={"id": ("in", detail_ids), "order_id": ("eq", order.id)},
        )
        missing = sorted(set(detail_ids) - {d.id for d in details})
        if missing:
            raise CustomException(msg=f"认领失败：子订单行 {missing[:20]} 不存在，或不属于订单 {order_no}")
        # 报错点名用「商品名／规格 · 实发件数（id）」而不是裸 id：操作人是在展开行那张子订单表里
        # 找行的人，拿数据库 id 找不到那一行（§26.6）。
        unshipped = sorted((d for d in details if not d.is_erp_shipped), key=lambda d: d.id)
        if unshipped:
            raise CustomException(
                msg=f"认领失败：勾选的 {len(unshipped)} 行没走 ERP 发货，单号写进去也匹配不上——{self._name_details(unshipped)}"
            )
        distributed = sorted((d for d in details if d.distribution_order_type), key=lambda d: d.id)
        if distributed:
            raise CustomException(
                msg=f"认领失败：勾选的 {len(distributed)} 行是分销订单，分销单不参与账单匹配（ADR-0036）——{self._name_details(distributed)}"
            )
        # §26.6：目标订单批次**已确认不再是拒绝理由**（原「批次已确认，订正只能改数据库」那道闸整条删除）。
        # 运单号挂进已确认的订单批次之后，钱仍然只在确认这份账单批次那一刻按覆盖式回写摊进去，触及已确认
        # 订单批次时 `_note_confirmed_order_batches` 会在账单批次备注里留「改动了 order-YYYY-MM，需重算利润」。
        # 例外通道的范围与代价见 ADR-0033 末尾修订块。
        old_waybills = sorted({d.tracking_no for d in details if d.tracking_no and d.tracking_no != row.waybill_no})
        if old_waybills:
            holders: Sequence[CourierBillModel] = await self._bill_crud.get_list(
                search={"waybill_no": ("in", old_waybills), "status": ("in", list(ORIGINAL_STATUSES))},
            )
            clash = [h for h in holders if h.id != row.id]
            if clash:
                clash_nos = {h.waybill_no for h in clash}
                victims = sorted((d for d in details if d.tracking_no in clash_nos), key=lambda d: d.id)
                raise CustomException(
                    msg="认领失败：勾选行原来的运单号正被别的账单原件行用着，换掉那一票就没了归属——"
                        + "、".join(f"{h.waybill_no}（账单行 {h.id}，批次 {h.import_batch_id}）" for h in clash[:20])
                        + f"；要受影响的是：{self._name_details(victims)}"
                )
        weight_sum = sum(((d.weight_kg or Decimal("0")) * (d.ship_qty or 0) for d in details), Decimal("0"))
        qty_sum = sum((d.ship_qty or 0) for d in details)
        if weight_sum <= 0 and qty_sum <= 0:
            raise CustomException(
                msg=f"认领失败：勾选的子订单行重量与实发件数全是 0，运单 {row.waybill_no} 这一票的运费摊不下去"
                    "（确认批次时同样会被拒，别等到那一步才发现）"
            )
        return list(details)

    @staticmethod
    def _name_details(details: Sequence[OrderDetailModel]) -> str:
        """认领报错里点名子订单行：商品名／规格 · 实发件数（id）。
        裸 id 操作人在界面上找不到对应行——那句话是给人去展开行里核对用的，id 只留作后备。"""
        shown = sorted(details, key=lambda d: d.id)[:20]
        return "、".join(
            f"{d.product_name}／{d.sku_name or '不分规格'} · 实发 {d.ship_qty} 件（id {d.id}）" for d in shown
        )

    async def _carrier_name(self, bill_type: str) -> str:
        """快递公司显示名（只进备注给人看，运费摊分从头到尾不看快递公司，AI26）。规则删了就退回编码。"""
        rule = await BillParseRuleCRUD(self.auth, self.db).get(bill_type=bill_type)
        return rule.carrier_name if rule else bill_type

    async def _require_status_in(
        self, ids: list[int], *, allowed: tuple[str, ...], allowed_label: str, action: str,
    ) -> list[CourierBillModel]:
        """取出状态在 `allowed` 内、且所属批次未确认的账单行，否则报错。"""
        if not ids:
            raise CustomException(msg="操作失败，账单行ID不能为空")
        rows: Sequence[CourierBillModel] = await self._bill_crud.get_list(search={"id": ("in", ids)})
        found = {r.id: r for r in rows}
        missing = [i for i in ids if i not in found]
        if missing:
            raise CustomException(msg=f"操作失败，账单行不存在：{missing[:20]}")
        wrong = [r.id for r in rows if r.status not in allowed]
        if wrong:
            raise CustomException(msg=f"只有{allowed_label}行可以{action}，以下行状态不符：{wrong[:20]}")
        confirmed: list[str] = []
        for batch_no in {r.import_batch_id for r in rows if r.import_batch_id}:
            batch = await self.batch_service.get_by_batch_no(batch_no)
            if batch and batch.status == BATCH_STATUS_CONFIRMED:
                confirmed.append(batch.batch_no)
        if confirmed:
            raise CustomException(msg=f"批次已确认，一律不动（订正只能改数据库）：{'、'.join(sorted(confirmed))}")
        return list(rows)

    # ── 批次作废 / 空批次声明 / 门禁预检 ─────────────────────────────────

    async def discard_bill_batch(self, batch_no: str) -> str:
        """作废未确认的账单批次：连行带批次**物理删除**（ADR-0033）。

        框架 `CRUDBase.delete` 是软删，而 `ecommerce_import_batch.batch_no` 的数据库唯一索引不认
        `is_deleted`：软删之后同月同类型重导照样撞键，做成「界面看着清干净了、导入却报重复」。
        作废的语义是「这批账单从没导进来过」，所以账单行必须一起删——留着它们还会参与判重和摊分。
        已确认批次一律不动。
        """
        batch = await self.batch_service.get_by_batch_no(batch_no)
        if not batch:
            raise CustomException(msg=f"批次 {batch_no} 不存在")
        if batch.file_category != FILE_CATEGORY_BILL:
            raise CustomException(msg=f"批次 {batch_no} 不是账单批次；订单批次没有作废入口，订正订单只能改数据库")
        if batch.status == BATCH_STATUS_CONFIRMED:
            raise CustomException(msg=f"批次 {batch_no} 已确认，不允许作废；如需订正只能直接修改数据库")
        bill_rows, batch_rows = await self._discard_batch(batch)
        return f"批次 {batch_no} 已作废：物理删除账单行 {bill_rows} 条、批次记录 {batch_rows} 条"

    async def _discard_batch(self, batch: ImportBatchModel) -> tuple[int, int]:
        """连行带批次物理删除（含软删行）；前提由调用方判（是账单批次、未确认）。"""
        bill_rows = await self.db.execute(delete(CourierBillModel).where(CourierBillModel.import_batch_id == batch.batch_no))
        batch_rows = await self.db.execute(delete(ImportBatchModel).where(ImportBatchModel.batch_no == batch.batch_no))
        await self.db.flush()
        return bill_rows.rowcount or 0, batch_rows.rowcount or 0

    async def declare_empty_batches(self, bill_month: str, bill_types: list[str]) -> str:
        """声明「这个月这几家没有账单」：为零行未确认批次建档，让订单批次门禁能走过去。

        门禁问的是「该月该有几家账单」，答案在解析规则表里；确实没账单的那几家必须有东西表态，
        否则订单批次永远确认不了。已有批次的类型跳过并报出——声明不覆盖、也不删除任何东西。
        """
        bill_month = validate_month(bill_month, field_name="出账月份")
        if not bill_types:
            raise CustomException(msg="声明失败：请至少选择一个账单类型")
        # 只认启用中的规则（与导入同一把尺子）：停用的类型本来就不在门禁名单里，为它声明零行批次没有意义
        rules = await BillParseRuleCRUD(self.auth, self.db).get_list(
            search={"bill_type": ("in", sorted(set(bill_types))), "status": 0},
        )
        known = {r.bill_type for r in rules}
        unknown = sorted(set(bill_types) - known)
        if unknown:
            raise CustomException(
                msg=f"声明失败：以下账单类型没有启用中的解析规则（未建档或已停用），"
                    f"请先在「解析规则管理」处理：{'、'.join(unknown)}"
            )

        created: list[str] = []
        skipped: list[str] = []
        for bill_type in sorted(known):
            if await self.batch_service.get_by_batch_no(build_batch_no(FILE_CATEGORY_BILL, bill_month, bill_type)):
                skipped.append(bill_type)
                continue
            batch = await self.batch_service.create_batch(
                file_category=FILE_CATEGORY_BILL, batch_month=bill_month, sub_category=bill_type,
            )
            await self.batch_service.append_remark(batch, f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 人工声明本月无账单；")
            created.append(batch.batch_no)
        msg = f"已声明 {bill_month} 无账单，生成零行批次：{'、'.join(created)}"
        if skipped:
            msg += f"；{'、'.join(skipped)} 已有批次，未作改动"
        return msg

    async def bill_gate_gaps(self, bill_month: str) -> BillGateGapSchema:
        """订单批次确认门禁的只读预检：该月缺哪几项（未确认的账单批次 / 完全没批次的类型 / 一条规则都没有）。"""
        # 月份与确认接口同一把尺（§15.6）：`2026-8` 这类写法从前被放过、回「无缺口」，
        # 预检报「齐了」而确认接口报「格式错误」，等于把门禁的只读影子变成了假绿灯。
        bill_month = validate_month(bill_month, field_name="出账月份")
        gaps = await self.batch_service.bill_gate_gaps(
            bill_month, active_bill_types=await BillParseRuleCRUD(self.auth, self.db).active_bill_types(),
        )
        # ④ 挂不到批次的账单行数：只有手工删了批次行、没删干净名下账单行才会出现（ADR-0033 2026-10-29）。
        # 它不进 `passed` 判定（批次都不在，本来就确认不了），但必须报出来——这种行按出账月份筛得到，
        # 而界面上的批次下拉只列还存在的批次、选不到它，不报警就等于让一笔脏数据在这一页隐形。
        # 走 ORM 外连接而不是 CRUD：CRUD 拼不出这个 join，而这里数的是系统健康度，不是业务可见范围。
        gaps.orphan_bill_rows = (
            await self.db.execute(
                select(func.count(CourierBillModel.id))
                .outerjoin(
                    ImportBatchModel,
                    and_(
                        ImportBatchModel.batch_no == CourierBillModel.import_batch_id,
                        ImportBatchModel.is_deleted.is_(False),
                    ),
                )
                .where(
                    CourierBillModel.bill_month == bill_month,
                    CourierBillModel.is_deleted.is_(False),
                    ImportBatchModel.id.is_(None),
                )
            )
        ).scalar_one()
        return gaps

    # ── 批次确认 + 运费覆盖式回写 ────────────────────────────────────────

    async def confirm_bill_batch(self, batch_no: str) -> BatchConfirmResultSchema:
        """确认账单批次：把原件行的运费覆盖式写回子订单，并刷主订单求和（ADR-0034）。

        确认即终态：还有虚假/重复未处理则拒绝；回写是覆盖不是累加，所以同一批次
        重复确认、或异常处理后再次确认，子订单上的运费成本都只反映当前原件行。
        """
        batch = await self.batch_service.get_by_batch_no(batch_no)
        if not batch:
            raise CustomException(msg=f"批次 {batch_no} 不存在")
        if batch.file_category != FILE_CATEGORY_BILL:
            raise CustomException(msg=f"批次 {batch_no} 不是账单批次，请走订单批次确认")
        if batch.status == BATCH_STATUS_CONFIRMED:
            raise CustomException(msg=f"批次 {batch_no} 已确认，不允许重复确认")
        # 一条启用中的解析规则都没有时不放行（§15.6 Z5）：账单是靠规则读进来的，规则表空了
        # 说明配置出了问题，此时确认等于给一批没人核对过的数据盖「可信」的章。
        if not await BillParseRuleCRUD(self.auth, self.db).active_bill_types():
            raise CustomException(
                msg="确认失败：当前没有任何启用中的账单解析规则，账单批次不能确认——请先到「解析规则」建档或启用"
            )

        blocking = await self._bill_crud.count(
            import_batch_id=("eq", batch_no),
            status=("in", list(BLOCKING_STATUSES)),
        )
        if blocking:
            raise CustomException(msg=f"确认失败：批次 {batch_no} 还有 {blocking} 行虚假/重复未处理（删除或改对后置已恢复）")

        rows: Sequence[CourierBillModel] = await self._bill_crud.get_list(
            search={"import_batch_id": ("eq", batch_no), "status": ("in", list(ORIGINAL_STATUSES))},
        )
        groups = await self._collect_waybill_groups(batch_no=batch_no, rows=rows)
        detail_groups = await self._load_shipped_details_by_waybill(set(groups))
        for waybill, group in groups.items():
            group.details = sorted(detail_groups.get(waybill, []), key=_detail_order_key)

        self._write_freight(groups)
        touched_orders = await self._refresh_order_aggregates(groups)
        warning = await self._note_confirmed_order_batches(batch, touched_orders)

        await self.batch_service.mark_confirmed(batch)
        await self._notify_profit_recalc(batch)

        return BatchConfirmResultSchema(
            batch_no=batch_no,
            message=f"批次 {batch_no} 已确认：原件行 {len(rows)} 行，运费已回写子订单 {len(groups)} 票",
            warning=warning,
        )

    async def _collect_waybill_groups(self, *, batch_no: str, rows: Sequence[CourierBillModel]) -> dict[str, _WaybillGroup]:
        """把本批次的原件行按运单号聚成摊分单元，整票金额取该运单号「全部」原件行之和。

        同运单号跨批次出现第二张原件行是允许的（把重复行人工改对后置「已恢复」）。回写是
        覆盖式的，若只按本批次的行求和，后确认的那个批次会把先确认批次的运费覆盖掉、钱静默
        消失；所以这里要跨批次取全集。未确认批次的行不计入——它们还是原始数据，没资格进成本。
        """
        waybills = {r.waybill_no for r in rows}
        all_originals: Sequence[CourierBillModel] = await self._bill_crud.get_list(
            search={"waybill_no": ("in", sorted(waybills)), "status": ("in", list(ORIGINAL_STATUSES))},
        )
        foreign = {r.import_batch_id for r in all_originals if r.import_batch_id} - {batch_no}
        confirmed_foreign = await self.batch_service.confirmed_batch_nos(foreign)
        groups: dict[str, _WaybillGroup] = {}
        for row in all_originals:
            if row.import_batch_id != batch_no and row.import_batch_id not in confirmed_foreign:
                continue
            groups.setdefault(row.waybill_no, _WaybillGroup(waybill_no=row.waybill_no)).bill_rows.append(row)
        return groups

    @staticmethod
    def _write_freight(groups: dict[str, _WaybillGroup]) -> None:
        """整票金额（同运单号多张原件行相加）按重量摊到子订单 freight_cost。

        重量全空/全0 → 退化按实发件数摊；件数也全0 → 拒绝确认（这一票的钱无处安放）。
        """
        unapportionable: list[str] = []
        for group in groups.values():
            if not group.details:
                unapportionable.append(f"{group.waybill_no}（查不到ERP发货且非分销的子订单）")
                continue
            keys = group.weight_keys
            if sum(keys, Decimal("0")) <= 0:
                keys = [Decimal(q) for q in group.qty_keys]
            if sum(keys, Decimal("0")) <= 0:
                unapportionable.append(f"{group.waybill_no}（重量与实发件数均为0）")
                continue
            for detail, share in zip(group.details, _apportion(group.total_amount, keys), strict=True):
                detail.freight_cost = share
        if unapportionable:
            preview = "、".join(unapportionable[:20])
            raise CustomException(
                msg=f"确认失败：以下运单无法摊分运费，请先处理后重试：{preview}"
                + (f" 等 {len(unapportionable)} 票" if len(unapportionable) > 20 else ""),
            )

    async def _refresh_order_aggregates(self, groups: dict[str, _WaybillGroup]) -> list[OrderModel]:
        """主订单运费成本求和 = Σ该订单全部子订单 freight_cost（NULL 按0计入）。"""
        order_ids = {d.order_id for group in groups.values() for d in group.details}
        if not order_ids:
            return []
        all_details: Sequence[OrderDetailModel] = await OrderDetailCRUD(self.auth, self.db).get_list(
            search={"order_id": ("in", sorted(order_ids))},
        )
        sums: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
        for d in all_details:
            sums[d.order_id] += d.freight_cost or Decimal("0")
        orders: Sequence[OrderModel] = await OrderCRUD(self.auth, self.db).get_list(search={"id": ("in", sorted(order_ids))})
        for order in orders:
            order.freight_cost_amount = round_cent(sums[order.id])
        await self.db.flush()
        return list(orders)

    async def _note_confirmed_order_batches(self, batch: ImportBatchModel, touched_orders: Sequence[OrderModel]) -> str | None:
        """回写触及已确认订单批次：在本账单批次详情留一行提示，不自动重算利润。"""
        confirmed_nos = sorted({o.import_batch_id for o in touched_orders if o.import_batch_id})
        if not confirmed_nos:
            return None
        lines: list[str] = []
        for order_batch_no in confirmed_nos:
            order_batch = await self.batch_service.get_by_batch_no(order_batch_no)
            if order_batch and order_batch.status == BATCH_STATUS_CONFIRMED:
                lines.append(f"改动了 {order_batch_no}，需重算利润；")
        if not lines:
            return None
        await self.batch_service.append_remark(batch, "".join(lines))
        return "".join(lines)

    async def _notify_profit_recalc(self, batch: ImportBatchModel) -> None:
        """TODO(挂账)：运费回写触及已确认订单批次时，自动通知并触发利润重算。

        本期只在批次详情的 remark 里留提示行，由人工去利润页重算；通知渠道与重算入口
        等利润模块落地后再补。
        """


def _prev_month(month: str) -> str:
    """``"YYYY-MM"`` 的上一个月（认领的「往前一档」窗口用，§26.6）。入参已由导入侧的月份尺校验过格式。"""
    year, mon = int(month[:4]), int(month[5:7])
    return f"{year - 1}-12" if mon == 1 else f"{year}-{mon - 1:02d}"


def _detail_order_key(detail: OrderDetailModel) -> tuple[date, int]:
    """子订单"最早"口径：发货时间最早者优先，时间相同按ID。用于跨店铺判定与余数补给。"""
    return (detail.ship_time.date() if detail.ship_time else date.max, detail.id)


def _weight_province_reason(row: RawBillRow, details: list[OrderDetailModel]) -> str | None:
    """重量/省份异常说明文案；两者共用一个行状态，原因里写清是哪一类。

    理论重量为 0（SKU 重量没配）时不能写成「超理论 0kg + 容差 0.05kg」——那看起来像算错了，
    实际是缺数据；文案直接 point 到「SKU 重量缺失」。收件省单元格空着（导出给 `-`）则跳过
    省份比较：没有证据就不判异常。
    """
    reasons: list[str] = []
    if row.bill_weight is not None:
        theory = sum(((d.weight_kg or Decimal("0")) * (d.ship_qty or 0) for d in details), Decimal("0"))
        if theory <= 0:
            reasons.append(f"本票子订单 SKU 重量缺失，理论重量按 0 计，账单重量 {row.bill_weight}kg")
        else:
            ceiling = theory + WEIGHT_TOLERANCE_FIXED + theory * WEIGHT_TOLERANCE_RATE
            if row.bill_weight > ceiling:
                reasons.append(f"账单重量 {row.bill_weight}kg 超理论 {theory}kg+容差 {ceiling - theory}kg")
    if row.receiver_province:
        order_provinces = {normalize_province(d.order.province) for d in details if d.order and d.order.province}
        if order_provinces and row.receiver_province not in order_provinces:
            reasons.append(f"收件省 {row.receiver_province} 与订单 {'/'.join(sorted(order_provinces))} 不符")
    return "；".join(reasons) or None
