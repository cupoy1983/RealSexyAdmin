from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, Decimal
from typing import Any

from fastapi import UploadFile
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.exceptions import CustomException
from app.plugin.module_ecommerce.common.import_batch.model import (
    BATCH_STATUS_CONFIRMED,
    FILE_CATEGORY_ORDER,
    ImportBatchModel,
    build_batch_no,
)
from app.plugin.module_ecommerce.common.import_batch.schema import BatchConfirmResultSchema
from app.plugin.module_ecommerce.common.import_batch.service import (
    EMPTY_IMPORT_FILE_MSG,
    ImportBatchService,
    validate_month,
)
from app.plugin.module_ecommerce.common.numbers import MONEY_QUANT, over_cent, round_cent, round_weight
from app.plugin.module_ecommerce.logistics.crud import BillParseRuleCRUD
from app.plugin.module_ecommerce.store.service import StoreService
from app.utils.common_util import search_to_dict

from .crud import OrderCRUD, OrderDetailCRUD, SkuCostCRUD
from .importers.base import (
    DISTRIBUTION_TYPE_NORMAL,
    DISTRIBUTION_TYPE_PROXY,
    OrderDataSource,
    RawOrderDetailRecord,
    RawOrderRecord,
    normalize_merchant_code,
    normalize_spec_code,
)
from .importers.excel_source import ExcelOrderDataSource
from .model import OrderDetailModel, OrderModel, SkuCostModel
from .scam_reconciliation import reconcile_orders
from .schema import (
    OrderDetailOutSchema,
    OrderOutSchema,
    OrderQueryParam,
    ScamApprovalDetailSchema,
    ScamReconciliationResultSchema,
    SkuCostCreateSchema,
    SkuCostOutSchema,
    SkuCostQueryParam,
    SkuCostUpdateSchema,
)


def _ceil_cent(value: Decimal) -> Decimal:
    """见位进分（设计文档 §15.1）：小数第 3 位及以后只要有非零数字就往上进 1 分。

    只管**算出来的**金额（`computed_*` 与主订单成本聚合），且只在这一条路径上用，所以留在本模块；
    快麦报过来的源值走 `common/numbers.py::round_cent`。
    旧的两种行为各在一侧——导入把未量化的乘积交给 MySQL DECIMAL 舍入（1.025→1.03），
    更新用默认 ROUND_HALF_EVEN（1.025→1.02），于是同一份数据「只导入」与「导入后再更新」算出不同的钱。
    """
    return value.quantize(MONEY_QUANT, rounding=ROUND_CEILING)


def _parse_datetime(value: str | None) -> datetime | None:
    """把数据源的时间字符串转为原生 datetime（框架约定 ORM 写入用原生 datetime）。"""
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
    return None


def _remark_segments(value: str | None) -> list[str]:
    """备注按拼接分隔符 `；` 切成整段（两边 trim、空段丢弃），保序。

    备注合并只能整段比对（§5.1b 第 7 步）：库里那些段就是历次 `旧；新` 拼出来的原子，
    段与段之间没有语义关系，拿子串比会把一小句误判成「已经在了」。
    """
    if not value:
        return []
    return [seg for raw in value.split("；") if (seg := raw.strip())]


def _derive_refund_status(buyer_paid_amount: Decimal, refund_amount: Decimal) -> str:
    """按主订单聚合金额推导退款状态：0=无退款，退款≥已付=整单退款，其余=部分退款。"""
    if refund_amount <= 0:
        return "none"
    if refund_amount >= buyer_paid_amount:
        return "full"
    return "partial"


# 订单更新中「变动即应用」的 9 个比对字段（ADR-0032 修订；第 9 项随 ADR-0036/AC15 加入）：
# (字段名, 中文标签, 类型)。类型 "cent" = 快麦报过来的钱，按四舍五入到分比较（§20.4 AF5=甲）；
# "weight" = 重量，按四舍五入到 3 位比较（§21.1 AG1）。**两把尺都在 `common/numbers.py` 只有一份**，
# 导入侧调的是同一个函数——「导入与更新复用同一个量化」是 ADR-0039 那条规矩的落点，不是形容词。
# 实发数量不在其中——它是不变式校验，变化即整批中止；实退数量在此，变动即应用并联动重算成本。
# erp_shipped_cost/erp_return_cost 比对快麦原值列，仅作展示同步；系统计算成本(computed_*)由主表回查重算，不比对快麦原值。
# weight_kg 也要比对：运费摊分完全按重量算，SKU 重量改了却不应用，摊出来的成本就一直是旧值。
_UPDATE_DIFF_FIELDS: list[tuple[str, str, str]] = [
    ("detail_status", "明细状态", "str"),
    ("paid_amount", "已付金额", "cent"),
    ("refund_amount", "退货金额", "cent"),
    ("return_qty", "实退数量", "qty"),
    ("erp_shipped_cost", "发货成本", "cent"),
    ("erp_return_cost", "退货成本", "cent"),
    ("after_sales_no", "售后工单号", "str"),
    ("weight_kg", "重量kg", "weight"),
    ("distribution_order_type", "分销订单", "dist"),
]

# 数量不变式校验字段：(字段名, 中文标签)——实发数量一变即整批中止（Q35）
_UPDATE_QTY_FIELDS: list[tuple[str, str]] = [
    ("ship_qty", "实发数量"),
]


@dataclass
class OrderUpdateResult:
    """订单更新结果。

    ok=False 时 controller 返回**业务错误码而不是抛异常**：HTTP 状态仍给 200，让更新页面
    用整段弹窗展示中止清单（框架的 http 拦截器对 HTTP 400 会无条件弹 toast，长清单会被截断）。
    """

    ok: bool
    msg: str


def _fmt_remark_value(value: Any) -> str:
    """更新备注里旧值/新值的展示格式：None/空串显示为「空」。"""
    if value is None or value == "":
        return "空"
    return str(value)


class OrderService:
    """订单与商品成本模块服务层"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        self.auth = auth
        self.db = db

    async def details_by_order(self, order_id: int) -> list[OrderDetailOutSchema]:
        """按主订单ID返回子订单列表（列表页行展开懒加载用，不分页）。

        这里**不带**缺账单筛选（§24，AK 轮）：那条尺子住在 `crud.missing_bill_conditions`，
        只挂在主订单列表的 EXISTS 上——筛完主订单再点开，看的就是这一单的全部子订单。
        """
        rows: Sequence = await OrderDetailCRUD(self.auth, self.db).get_list(
            search={"order_id": ("eq", order_id)}, order_by=[{"id": "asc"}],
        )
        return [OrderDetailOutSchema.model_validate(r) for r in rows]

    async def confirm_order_batch(self, batch_no: str) -> BatchConfirmResultSchema:
        """确认订单批次（§5.6 门禁）：同月账单批次必须全部已确认，利润才认这批。

        成本缺失只软提示不硬拦——账单天然可能晚到，卡在这里会把整个月的结算堵死；
        缺的那几票由订单列表的「只看缺账单」筛出来人工处理。
        """
        batch_service = ImportBatchService(self.auth, self.db)
        batch: ImportBatchModel | None = await batch_service.get_by_batch_no(batch_no)
        if not batch:
            raise CustomException(msg=f"批次 {batch_no} 不存在")
        if batch.file_category != FILE_CATEGORY_ORDER:
            raise CustomException(msg=f"批次 {batch_no} 不是订单批次，请走账单批次确认")
        if batch.status == BATCH_STATUS_CONFIRMED:
            raise CustomException(msg=f"批次 {batch_no} 已确认，不允许重复确认")

        # 账单类型清单由调用方取好再传进门禁：common 不反向依赖 logistics，否则两个 service 成环
        await batch_service.assert_bill_batches_confirmed(
            batch.batch_month,
            active_bill_types=await BillParseRuleCRUD(self.auth, self.db).active_bill_types(),
        )

        warning: str | None = None
        orders: Sequence[OrderModel] = await OrderCRUD(self.auth, self.db).get_list(
            search={"import_batch_id": ("eq", batch_no)}, load_columns=[OrderModel.id],
        )
        if orders:
            missing = await OrderDetailCRUD(self.auth, self.db).count(
                order_id=("in", [o.id for o in orders]),
                # 与订单列表「只看缺账单」同一把尺（crud.missing_bill_conditions，§24）
                missing_bill=True,
            )
            if missing:
                warning = f"该批次还有 {missing} 条 ERP 发货子订单没有运费成本（缺账单），可在订单列表勾「只看缺账单」筛出来处理；不影响本次确认。"

        await batch_service.mark_confirmed(batch)
        return BatchConfirmResultSchema(
            batch_no=batch_no,
            message=f"批次 {batch_no} 已确认，{batch.batch_month} 的订单数据已放行给利润计算",
            warning=warning,
        )

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: OrderQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[OrderOutSchema]:
        offset = (page_no - 1) * page_size
        search_dict: dict[str, Any] = search_to_dict(search, {}) or {}
        return await OrderCRUD(self.auth, self.db).page(
            offset=offset,
            limit=page_size,
            order_by=order_by or [{"id": "asc"}],
            search=search_dict,
            preload=["store"],
            out_schema=OrderOutSchema,
        )

    async def import_orders(
        self,
        *,
        ship_month: str,
        file: UploadFile | None = None,
        data_source: OrderDataSource | None = None,
        source_params: dict[str, Any] | None = None,
    ) -> str:
        """导入订单（§5.1，步骤顺序不可打乱）。

        导入只负责新订单首次入库：数据中只要含任何已存在的订单号，整批报错、一单不入，
        已有订单的变动一律走 update_orders（ADR-0032）。

        1. 发货月份即批次（ADR-0033）：`order-2026-09` 已存在就不允许再导一次
        2. 任一主订单的归属月（最早一条发货时间所在月）不是所选月 → 判为选错文件/月份，整批中止
        3. 按「店铺编码」(kuaimai_shop_id) 自动建档 Store（存在则更新名称/平台，ADR-0024）
        4. 按系统订单号归并主订单，金额字段 = 子订单求和（ADR-0030）
        5. 子订单成本价从 sku_cost 主表取（不用 Excel 列覆盖，仅比对告警，ADR-0022）
        6. 计算 computed_product_cost(发货成本) / computed_return_cost(退货成本)，每条乘积按
           见位进分到分后才落库、才参与主订单求和（§15.1），与快麦「系统实发成本」「实退成本」
           比对，不一致告警交管理员核查
        7. cost_price 为 NULL 时标 cost_missing（ADR-0023）
        8. 分销订单行（标签含 `已生成采购单`）是 ADR-0022 的第一条例外：两个成本取快麦原值
           （`系统实发成本` / `实退成本`）、不置 cost_missing、不跑成本差异告警（ADR-0036）
        9. 文件里没有「订单标签」列 → 全部按非分销订单处理，并在结果里明写一句（AC8=乙）

        参数:
        - ship_month (str): 发货月份(YYYY-MM)，本次导入的归属批次
        - file (UploadFile | None): 上传的订单 Excel（走默认 ExcelOrderDataSource）
        - data_source (OrderDataSource | None): 显式指定数据源（未来 API 通路可传入）
        - source_params (dict | None): 传给数据源 fetch() 的额外参数

        返回:
        - str: 导入结果描述（成功条数 + 成本不一致告警）
        """
        ship_month = validate_month(ship_month, field_name="发货月份")
        raw_orders, source = await self._fetch_raw_orders(
            file=file, data_source=data_source, source_params=source_params,
        )
        self._assert_ship_month(raw_orders, ship_month=ship_month)

        # ADR-0032：先校验后落库——任一订单号已存在 → 整批报错，一单不入
        existing_nos: list[str] = []
        for raw in raw_orders:
            if await OrderCRUD(self.auth, self.db).exists(system_order_no=raw.system_order_no):
                existing_nos.append(raw.system_order_no)
        if existing_nos:
            preview = "、".join(existing_nos[:50])
            suffix = f" 等 {len(existing_nos)} 单" if len(existing_nos) > 50 else ""
            raise CustomException(msg=f"导入失败：以下订单号已存在，请改用「更新订单」：{preview}{suffix}")

        batch = await ImportBatchService(self.auth, self.db).create_batch(
            file_category=FILE_CATEGORY_ORDER, batch_month=ship_month,
        )
        batch_id = batch.batch_no
        data_source_name = "excel" if isinstance(source, ExcelOrderDataSource) else "api"

        store_cache: dict[str, int] = {}
        sku_cost_cache: dict[tuple[str | None, str | None], Decimal | None] = {}
        cost_mismatch_warnings: list[str] = []
        detail_count = 0

        for raw in raw_orders:
            await self._create_new_order(
                raw,
                batch_id=batch_id,
                data_source_name=data_source_name,
                store_cache=store_cache,
                sku_cost_cache=sku_cost_cache,
                cost_mismatch_warnings=cost_mismatch_warnings,
            )
            detail_count += len(raw.details)

        await ImportBatchService(self.auth, self.db).add_counts(
            batch, row_count=detail_count, order_count=len(raw_orders),
        )

        result = f"成功导入 {len(raw_orders)} 条主订单、{detail_count} 条子订单（批次 {batch_id}）"
        # 没有「汇总」行就没有对账基准，金额校验和会被整体跳过（§16 B2）——这事必须让人看见，
        # 而不是藏在 excel_source 的一行注释里：导错月份/被裁剪过的文件正是靠这把闸拦下的。
        if isinstance(source, ExcelOrderDataSource) and not source.summary_row_found:
            result += "\n注意：本次没读到「汇总」行（序号=汇总 那一行），未做金额校验和，买家已付/退款/运费收入没有对过账。"
        # 漏这一列等于整月分销订单被算成非分销、成本从 SKU 表取错了数，所以必须让人看见（AC8=乙，ADR-0036）
        if isinstance(source, ExcelOrderDataSource) and not source.order_tag_column_found:
            result += "\n注意：本次没读到「订单标签」列，全部按非分销订单处理。"
        if cost_mismatch_warnings:
            result += f"\n成本不一致告警（{len(cost_mismatch_warnings)}条，请管理员核查）:\n" + "\n".join(
                cost_mismatch_warnings[:200]
            )
            if len(cost_mismatch_warnings) > 200:
                result += f"\n……（其余 {len(cost_mismatch_warnings) - 200} 条略）"
        return result

    @staticmethod
    def _assert_ship_month(raw_orders: list[RawOrderRecord], *, ship_month: str) -> None:
        """按主订单判归属月，与所选月份不符即整份不落库（ADR-0032，零容忍）。

        归属月 = 这张单**最早**一条发货时间所在月；跨月主订单（快麦补发是常态）整体归最早
        发货月那个批次，迟发的那几条子订单跟着走，不单独报错。全单都没有发货时间时无从判定，
        跳过（未发货行已在数据源剔除）。
        """
        mismatches: list[str] = []
        for raw in raw_orders:
            months = [st.strftime("%Y-%m") for st in map(_parse_datetime, (d.ship_time for d in raw.details)) if st]
            if months and min(months) != ship_month:
                mismatches.append(f"{raw.system_order_no}({min(months)})")
        if mismatches:
            preview = "、".join(mismatches[:20])
            suffix = f" 等 {len(mismatches)} 张" if len(mismatches) > 20 else ""
            raise CustomException(
                msg=f"发货月份不符：{len(mismatches)} 张主订单的最早发货时间不在所选月份 {ship_month}，整份不落库；"
                    f"请按月份分开导出或改用对应月份的批次。示例：{preview}{suffix}"
            )

    async def _fetch_raw_orders(
        self,
        *,
        file: UploadFile | None,
        data_source: OrderDataSource | None,
        source_params: dict[str, Any] | None,
    ) -> tuple[list[RawOrderRecord], OrderDataSource]:
        """读取上传文件/数据源并解析为 RawOrderRecord 列表（导入与更新共用）。

        0 字节要在交给数据源之前拦住：先前用 `if contents:` 决定要不要塞这个关键字参数，
        空上传时 contents 是假值 → 必填参数缺失 → 抛未捕获的 TypeError（HTTP 500），
        数据源里那句「导入文件为空」根本走不到。
        """
        source = data_source
        fetch_kwargs: dict[str, Any] = dict(source_params or {})
        if source is None:
            if file is None:
                raise CustomException(msg="需要提供文件或数据源")
            contents = await file.read()
            await file.close()
            if not contents:
                raise CustomException(msg=EMPTY_IMPORT_FILE_MSG)
            fetch_kwargs["contents"] = contents
            source = ExcelOrderDataSource()

        raw_orders: list[RawOrderRecord] = await source.fetch(**fetch_kwargs)
        if not raw_orders:
            raise CustomException(msg=EMPTY_IMPORT_FILE_MSG)
        return raw_orders, source

    async def _create_new_order(
        self,
        raw: RawOrderRecord,
        *,
        batch_id: str,
        data_source_name: str,
        store_cache: dict[str, int],
        sku_cost_cache: dict[tuple[str | None, str | None], Decimal | None],
        cost_mismatch_warnings: list[str],
        detail_extra: dict[str, Any] | None = None,
    ) -> OrderModel:
        """新建一张主订单及其全部子订单（§5.1 口径；导入与订单更新的整单新建分支共用）。

        参数 detail_extra 会合并进每条子订单落库字典（更新流程用它写入「新订单」备注）。
        """
        store_id = await self._ensure_store_id(raw, store_cache)

        shipped_cost_sum = Decimal("0")
        return_cost_sum = Decimal("0")
        detail_rows: list[dict[str, Any]] = []
        for d in raw.details:
            row = await self._build_detail_row(raw, d, sku_cost_cache, cost_mismatch_warnings)
            shipped_cost_sum += row["computed_product_cost"]
            return_cost_sum += row["computed_return_cost"]
            if detail_extra:
                row.update(detail_extra)
            detail_rows.append(row)

        order_data = {
            "system_order_no": raw.system_order_no,
            "store_id": store_id,
            "platform": raw.platform,
            "order_status": raw.order_status,
            "buyer_paid_amount": raw.buyer_paid_amount,
            "refund_amount": raw.refund_amount,
            "refund_status": _derive_refund_status(raw.buyer_paid_amount, raw.refund_amount),
            "shipped_cost_amount": shipped_cost_sum,
            "return_cost_amount": return_cost_sum,
            "freight_income": raw.freight_income,
            "province": raw.province,
            "city": raw.city,
            "system_remark": raw.system_remark,
            "seller_remark": raw.seller_remark,
            "is_shua_order": raw.is_shua_order,
            "data_source": data_source_name,
            "import_batch_id": batch_id,
        }
        order_obj: OrderModel = await OrderCRUD(self.auth, self.db).create(data=order_data)

        for row in detail_rows:
            row["order_id"] = order_obj.id
            await OrderDetailCRUD(self.auth, self.db).create(data=row)
        return order_obj

    async def _build_detail_row(
        self,
        raw: RawOrderRecord,
        d: RawOrderDetailRecord,
        sku_cost_cache: dict[tuple[str | None, str | None], Decimal | None],
        cost_mismatch_warnings: list[str],
    ) -> dict[str, Any]:
        """单条子订单 → 落库字典：成本价取 sku_cost 主表、算计算成本、不一致告警（§5.1 步骤4-6）。

        分销订单行是 ADR-0022 的第一条例外（ADR-0036、§5.1 第 7 步）：这一行**不查成本主表**，
        三个数全取快麦原值——`computed_product_cost` 取 `系统实发成本`、`computed_return_cost` 取
        `实退成本`（空或 0 → 0）、`cost_price` 取 `成本价(SKU)`；也不跑差异告警、
        不置 `cost_missing`——分销的货不是我们发的，也就没有退货可言。
        那两格钱在读文件时已按四舍五入整到分（§20.4 AF5=甲），所以这里的 `_ceil_cent` 原样通过，
        计算成本与快麦原值**逐字相等**。
        其余数值——`weight_kg`（3 位）、`excel_cost_price`（4 位）、`paid_amount`/`refund_amount`（2 位）——
        都由数据源在**读那一格那一步**量好了（ADR-0039），本函数原样透传、不再量第二次；
        更新路径比对的也是同一个函数产出的值，两条路径不会有第二个数。
        """
        distribution_order_type = d.distribution_order_type
        if distribution_order_type is None:
            # 文件里根本没有「订单标签」列 → 按非分销落库（AC8=乙：缺列照样导入，只在结果里提示）
            distribution_order_type = DISTRIBUTION_TYPE_NORMAL
        is_distribution = distribution_order_type == DISTRIBUTION_TYPE_PROXY

        if is_distribution:
            cost_price = d.excel_cost_price
            computed_product_cost = _ceil_cent(d.erp_shipped_cost or Decimal("0"))
            computed_return_cost = _ceil_cent(d.erp_return_cost or Decimal("0"))
        else:
            cost_price = await self._lookup_sku_cost_price(d.main_sku_code, d.sku_code, sku_cost_cache)
            unit = cost_price or Decimal("0")
            computed_product_cost = _ceil_cent(unit * d.ship_qty)
            computed_return_cost = _ceil_cent(unit * d.return_qty)

        if not is_distribution:
            self._collect_cost_warnings(raw.system_order_no, d, cost_price, cost_mismatch_warnings)

        return {
            "source_detail_id": d.source_detail_id,
            "platform_order_no": d.platform_order_no,
            "order_type": d.order_type,
            "detail_status": d.detail_status,
            "sku_code": d.sku_code,
            "main_sku_code": d.main_sku_code,
            "sku_name": d.sku_name,
            "product_name": d.product_name,
            "weight_kg": d.weight_kg,
            "cost_price": cost_price,
            "ship_qty": d.ship_qty,
            "return_qty": d.return_qty,
            "computed_product_cost": computed_product_cost,
            "computed_return_cost": computed_return_cost,
            "cost_missing": (not is_distribution) and bool(d.main_sku_code) and cost_price is None,
            "paid_amount": d.paid_amount,
            "refund_amount": d.refund_amount,
            "warehouse_name": d.warehouse_name,
            "courier_template": d.courier_template,
            "tracking_no": d.tracking_no,
            "is_erp_shipped": d.is_erp_shipped,
            "distribution_order_type": distribution_order_type,
            "pay_time": _parse_datetime(d.pay_time),
            "ship_time": _parse_datetime(d.ship_time),
            "finish_time": _parse_datetime(d.finish_time),
            "after_sales_no": d.after_sales_no,
            "excel_cost_price": d.excel_cost_price,
            "erp_shipped_cost": d.erp_shipped_cost,
            "erp_return_cost": d.erp_return_cost,
        }

    async def _ensure_store_id(self, raw: RawOrderRecord, cache: dict[str, int]) -> int:
        """按快麦店铺id自动建档并返回 store_id，同批次内缓存避免重复查询（ADR-0024）。"""
        cached = cache.get(raw.kuaimai_shop_id)
        if cached is not None:
            return cached
        store = await StoreService(self.auth, self.db).ensure_store(
            kuaimai_shop_id=raw.kuaimai_shop_id, store_name=raw.store_name, platform=raw.platform,
        )
        cache[raw.kuaimai_shop_id] = store.id
        return store.id

    async def _lookup_sku_cost_price(
        self,
        main_sku_code: str | None,
        sku_code: str | None,
        cache: dict[tuple[str | None, str | None], Decimal | None],
    ) -> Decimal | None:
        """两段式取成本价（§5.1 第7步，ADR-0022 唯一事实来源），同批次内缓存。

        规格商家编码有值→只按（主+规格）精确命中规格级行，命中不了不降级；
        规格商家编码为 NULL→按主编码取商品级行，但该主编码下已有规格行时不兜底
        （同一主编码可能挂着几十个成本不同的规格，取哪条都是错数）。
        """
        if not main_sku_code:
            return None
        key = (main_sku_code, sku_code)
        if key in cache:
            return cache[key]
        crud = SkuCostCRUD(self.auth, self.db)
        if sku_code:
            cost = await crud.get(main_sku_code=main_sku_code, sku_code=sku_code)
        else:
            cost = await crud.get(main_sku_code=main_sku_code, sku_code=("None", None))
            if cost is not None and await crud.count(main_sku_code=main_sku_code, sku_code=("not None", None)) > 0:
                cost = None
        price = cost.cost_price if cost else None
        cache[key] = price
        return price

    @staticmethod
    def _collect_cost_warnings(
        system_order_no: str,
        d: RawOrderDetailRecord,
        cost_price: Decimal | None,
        warnings: list[str],
    ) -> None:
        """快麦「成本价(SKU)」与系统成本主表的价不一致时告警（不覆盖主表，ADR-0022）。

        2026-11-02 AN20 把另外两条告警**整条删掉**（「系统实发成本 ≠ 自算发货成本」「实退成本 ≠ 自算退货成本」）：
        成本主表本来就有页面可查，价对不上是去改主表的事，不是每跑一遍导入就报一次的事。
        留下的这条比的是两个**原始价**：快麦那一格按落库列存 4 位（ADR-0039 修订块）、主表录入侧只准到分（AN13），
        `Decimal` 相等不看尾零，所以 `3.1100` 对 `3.11` 不报、`3.115` 对 `3.11` 必报。
        主表没录价的行不报：那一侧只有一个数，没有「两个价格」可比（AN20 唯一的例外）。
        原先 `excel_cost_price` 带原精度去比库里 4 位，会报出「文件 3.11094 / 主表 3.1109」这种查无实据的假警
        （§21.1 AG2）——告警是业务方决定「该不该去补成本」的依据，假报警的代价是真报警被忽略。
        """
        code_label = " / ".join(c for c in (d.main_sku_code, d.sku_code) if c) or "无商家编码"
        if cost_price is not None and d.excel_cost_price is not None and d.excel_cost_price != cost_price:
            warnings.append(
                f"订单 {system_order_no} 明细 {d.source_detail_id} SKU {code_label}: "
                f"成本价(SKU) {d.excel_cost_price} 与系统成本价 {cost_price} 不一致，请到快麦把该 SKU 的成本价改对"
            )

    async def update_orders(
        self,
        *,
        ship_month: str,
        file: UploadFile | None = None,
        data_source: OrderDataSource | None = None,
        source_params: dict[str, Any] | None = None,
    ) -> OrderUpdateResult:
        """订单更新（§5.1b，ADR-0032）：两遍处理、整批原子。

        pass1 只校验不写库：目标批次必须存在且未确认；文件里任一主订单的归属月必须就是
        所选月份；任一订单出现「库里有、新数据里没有」的子订单，或任一已有子订单的实发数量
        变化（数量不变式），或已有主订单省市变化，或扫到的主订单属于其他批次
        → 整批任何变动都不应用，返回 ok=False。**中止一律不写更新备注**，原因只报一次。

        pass2 统一应用：9 字段 diff（变动写新值 + 「[时间戳] 字段 旧值→新值；」追加更新
        备注）、新子订单插入（记「新增子订单」）、未入库订单整单创建并挂本次所选月份的批次
        （更新不建批次，见 `require_unconfirmed`）、有变动的订单按导入聚合口径重算主订单汇总。
        """
        ship_month = validate_month(ship_month, field_name="发货月份")
        batch_service = ImportBatchService(self.auth, self.db)
        target_batch_no = build_batch_no(FILE_CATEGORY_ORDER, ship_month)
        # 目标批次必须已存在且未确认：更新不新建批次，已确认的一律不动
        target_batch = await batch_service.require_unconfirmed(target_batch_no)

        raw_orders, source = await self._fetch_raw_orders(
            file=file, data_source=data_source, source_params=source_params,
        )
        # 更新新建的订单要挂进本月批次，所以归属月这道闸与导入完全一致
        self._assert_ship_month(raw_orders, ship_month=ship_month)

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        existing_orders: dict[str, OrderModel] = {}
        for raw in raw_orders:
            obj: OrderModel | None = await OrderCRUD(self.auth, self.db).get(system_order_no=raw.system_order_no)
            if obj:
                existing_orders[raw.system_order_no] = obj

        # ── pass 1：只校验，不写库 ──
        abort_errors: list[str] = []
        cross_batch: dict[str, list[str]] = defaultdict(list)
        db_details_cache: dict[str, list[OrderDetailModel]] = {}
        for raw in raw_orders:
            order = existing_orders.get(raw.system_order_no)
            if order is None:
                continue
            if (order.import_batch_id or "") != target_batch_no:
                # 跨批次的单成千上万很常见，只按批次号归组，别把每个单号都串进消息
                cross_batch[order.import_batch_id or "无批次（旧数据）"].append(raw.system_order_no)
                continue
            if (order.province or "") != (raw.province or "") or (order.city or "") != (raw.city or ""):
                abort_errors.append(
                    f"订单 {raw.system_order_no} 收货省份/城市发生变化"
                    f"（{order.province or ''}{order.city or ''}→{raw.province or ''}{raw.city or ''}），省市不允许变更"
                )
            db_details: Sequence[OrderDetailModel] = await OrderDetailCRUD(self.auth, self.db).get_list(
                search={"order_id": ("eq", order.id)},
            )
            db_details_cache[raw.system_order_no] = list(db_details)
            raw_by_id = {d.source_detail_id: d for d in raw.details}
            for dbd in db_details:
                new_d = raw_by_id.get(dbd.source_detail_id)
                if new_d is None:
                    abort_errors.append(
                        f"订单 {raw.system_order_no} 子订单 {dbd.source_detail_id}（SKU {dbd.sku_code}）在新数据中缺失"
                    )
                    continue
                for field_name, label in _UPDATE_QTY_FIELDS:
                    old_qty: int = getattr(dbd, field_name) or 0
                    new_qty: int = getattr(new_d, field_name) or 0
                    if new_qty != old_qty:
                        abort_errors.append(
                            f"订单 {raw.system_order_no} 子订单 {dbd.source_detail_id} "
                            f"{label}发生变化（{old_qty}→{new_qty}），已有子订单数量不允许变更"
                        )

        abort_errors = [
            f"批次 {owner}：{len(nos)} 张（示例：{'、'.join(nos[:3])} …）不属于本次所选发货月份 {ship_month}"
            for owner, nos in sorted(cross_batch.items())
        ] + abort_errors
        if abort_errors:
            # 中止一律不留痕：原因只在这里报一次，不写进子订单的更新备注（ADR-0032）
            return OrderUpdateResult(ok=False, msg="更新中止，本次未应用任何数据变动：\n" + "\n".join(abort_errors))

        # ── pass 2：统一应用 ──
        # 新建的订单挂本次所选月份的批次；批次在 pass1 前就已确认存在（更新不建批次）
        batch = target_batch
        batch_id = target_batch_no
        data_source_name = "excel" if isinstance(source, ExcelOrderDataSource) else "api"
        store_cache: dict[str, int] = {}
        sku_cost_cache: dict[tuple[str | None, str | None], Decimal | None] = {}
        cost_mismatch_warnings: list[str] = []
        updated_count = 0
        unchanged_count = 0
        new_detail_count = 0
        created_detail_count = 0
        created_orders: list[str] = []

        for raw in raw_orders:
            order = existing_orders.get(raw.system_order_no)
            if order is None:
                await self._create_new_order(
                    raw,
                    batch_id=batch_id,
                    data_source_name=data_source_name,
                    store_cache=store_cache,
                    sku_cost_cache=sku_cost_cache,
                    cost_mismatch_warnings=cost_mismatch_warnings,
                    detail_extra={"update_remark": f"[{ts}] 新订单，随订单更新整单创建"},
                )
                created_orders.append(raw.system_order_no)
                created_detail_count += len(raw.details)
                continue

            db_details = db_details_cache.get(raw.system_order_no)
            if db_details is None:
                db_details = list(
                    await OrderDetailCRUD(self.auth, self.db).get_list(search={"order_id": ("eq", order.id)})
                )
            db_by_id = {d.source_detail_id: d for d in db_details}
            order_changed = False

            for d in raw.details:
                dbd = db_by_id.get(d.source_detail_id)
                if dbd is None:
                    row = await self._build_detail_row(raw, d, sku_cost_cache, cost_mismatch_warnings)
                    row["order_id"] = order.id
                    row["update_remark"] = f"[{ts}] 新增子订单"
                    await OrderDetailCRUD(self.auth, self.db).create(data=row)
                    new_detail_count += 1
                    order_changed = True
                    continue
                changes, data = self._diff_detail(dbd, d, ts)

                dist_type: int = data.get("distribution_order_type", dbd.distribution_order_type or 0)
                if dist_type == DISTRIBUTION_TYPE_PROXY:
                    # 分销行整条跳过成本联动重算（§5.1b 第 5 步 / ADR-0036）：三个数定义上就是快麦原值，
                    # 回查主表等于把例外的数冲回被例外的那条规则。原值本身随第 4 步应用
                    # （含"文件里这一格空了"→落 0，AC28），所以这里跟着重取一次。
                    excel_price: Decimal | None = d.excel_cost_price
                    if dbd.cost_price != excel_price:
                        changes.append(
                            f"[{ts}] 成本价 {_fmt_remark_value(dbd.cost_price)}→{_fmt_remark_value(excel_price)}；"
                        )
                        data["cost_price"] = excel_price
                    shipped_cost: Decimal | None = data.get("erp_shipped_cost", dbd.erp_shipped_cost)
                    new_product_cost = _ceil_cent(shipped_cost or Decimal("0"))
                    if dbd.computed_product_cost != new_product_cost:
                        data["computed_product_cost"] = new_product_cost
                    return_cost: Decimal | None = data.get("erp_return_cost", dbd.erp_return_cost)
                    new_return_cost = _ceil_cent(return_cost or Decimal("0"))
                    if dbd.computed_return_cost != new_return_cost:
                        data["computed_return_cost"] = new_return_cost
                    # 刷成分销的那一瞬间要退出「待补充成本」清单（AC21=甲）：清单里它是正常数据，不是缺数
                    if dbd.cost_missing:
                        data["cost_missing"] = False
                else:
                    # 回查成本主表：以系统成本价为标准刷新 cost_price，并按新单价重算两个 computed_*（Q32/Q37/Q39）
                    # 改回非分销（1→0）也走这里，成本重新按主表口径算（§5.1b 第 5 步）
                    fresh_cost = await self._lookup_sku_cost_price(d.main_sku_code, d.sku_code, sku_cost_cache)
                    unit = fresh_cost or Decimal("0")
                    eff_return_qty: int = data.get("return_qty", dbd.return_qty or 0)
                    new_product_cost = _ceil_cent(unit * (dbd.ship_qty or 0))
                    new_return_cost = _ceil_cent(unit * eff_return_qty)
                    if dbd.cost_price != fresh_cost:
                        changes.append(
                            f"[{ts}] 成本价 {_fmt_remark_value(dbd.cost_price)}→{_fmt_remark_value(fresh_cost)}；"
                        )
                        data["cost_price"] = fresh_cost
                    # 缺失标记按口径无条件求值，不依附「成本价这一列碰巧变了」（§20.1 AF1）：
                    # 分销刷回非分销时，库里成本价与主表价常常都是 NULL，`NULL != NULL` 不成立会让整段被跳过，
                    # 于是这行按 0 成本进利润却永远不进「待补充成本」清单。只在值真的不同时才写，
                    # 否则每行都被判成有变动，更新结果里的「无变化 N 条」会全部归零。
                    new_missing = bool(d.main_sku_code) and fresh_cost is None
                    if dbd.cost_missing != new_missing:
                        data["cost_missing"] = new_missing
                    if dbd.computed_product_cost != new_product_cost:
                        data["computed_product_cost"] = new_product_cost
                    if dbd.computed_return_cost != new_return_cost:
                        data["computed_return_cost"] = new_return_cost

                # 派生的两个成本只要与库内值不同就落库，不依附「这一行还有别的字段变了」
                # （§5.1b 第 5 步）；它们不进更新备注，备注只记源字段变动。
                if changes:
                    data["update_remark"] = (dbd.update_remark or "") + "".join(changes)
                if data:
                    await OrderDetailCRUD(self.auth, self.db).update(id=dbd.id, data=data)
                    order_changed = True

            # 主订单级字段变更（Q40）：order_status 覆盖、备注合并；省市已在中止阶段拦截
            order_level: dict[str, Any] = {}
            if raw.order_status and (order.order_status or "") != (raw.order_status or ""):
                order_level["order_status"] = raw.order_status
            merged_sys = self._merge_remark(order.system_remark, raw.system_remark)
            if merged_sys is not None:
                order_level["system_remark"] = merged_sys
            merged_sel = self._merge_remark(order.seller_remark, raw.seller_remark)
            if merged_sel is not None:
                order_level["seller_remark"] = merged_sel

            if order_changed or order_level:
                await self._recompute_order_aggregates(order, raw, extra=order_level)
                updated_count += 1
            else:
                unchanged_count += 1

        msg = (
            f"成功更新 {updated_count} 单、新增子订单 {new_detail_count} 条、"
            f"新建订单 {len(created_orders)} 单（含 {created_detail_count} 条子订单，批次 {batch_id}）"
        )
        if created_orders:
            preview = "、".join(created_orders[:50])
            suffix = f" 等 {len(created_orders)} 单" if len(created_orders) > 50 else ""
            msg += f"（新建单号：{preview}{suffix}）"
        if unchanged_count:
            msg += f"；另有 {unchanged_count} 单无变化"
        # 「这一列不参与更新」是静默行为，不写出来没人知道这一轮没刷标记（§5.1b 第 11 步 / AC15 硬约束）
        if isinstance(source, ExcelOrderDataSource) and not source.order_tag_column_found:
            msg += "\n注意：本次没读到「订单标签」列：已有子订单的分销订单标记保持不变，新建的子订单按非分销订单处理。"
        # 批次 `row_count` 的口径是「这个批次带来了几行子订单」（导入路径同一把尺，见 `:298`）。
        # 整单新建那 K 单的行同样是本批次带来的，只加 `new_detail_count` 会让批次下拉里的「N 行」
        # 比库里真实行数少（§26.13 ③）。
        await batch_service.add_counts(
            batch,
            row_count=new_detail_count + created_detail_count,
            order_count=len(created_orders),
        )
        if cost_mismatch_warnings:
            msg += f"\n成本不一致告警（{len(cost_mismatch_warnings)}条，请管理员核查）:\n" + "\n".join(
                cost_mismatch_warnings[:200]
            )
            if len(cost_mismatch_warnings) > 200:
                msg += f"\n……（其余 {len(cost_mismatch_warnings) - 200} 条略）"
        return OrderUpdateResult(ok=True, msg=msg)

    @staticmethod
    def _diff_detail(
        dbd: OrderDetailModel, new: RawOrderDetailRecord, ts: str,
    ) -> tuple[list[str], dict[str, Any]]:
        """比对 9 个「变动即应用」字段，返回（变更备注片段列表，新值字典）。

        四列快麦原值的钱按四舍五入到分、重量按四舍五入到千克后三位比较；实退数量按整数比较；
        字符串 None 与空串视为相同；分销订单标记为 None（文件没有「订单标签」列）时整列跳过。
        **比较之前一定先量化再比，且量化用的是导入侧那同一个函数**（`common/numbers.py`，
        ADR-0039）：拿带第 3 位小数的原始值直接跟库里的分精度值比，这一格每轮都会被判成「有变化」
        （§20.4 AF5）；而重量那一格两侧不同尺更糟——导入交给数据库四舍五入、这里默认凑偶，
        同一份文件跑一次更新就把库里的 `0.003` 改写成 `0.002`，而重量是运费摊分的权重（§21.1 AG1）。
        成本联动重算（回查主表刷新 cost_price + computed_*）不在此处，由调用方在本函数返回后追加。
        """
        changes: list[str] = []
        data: dict[str, Any] = {}
        for field_name, label, kind in _UPDATE_DIFF_FIELDS:
            old_val = getattr(dbd, field_name)
            new_val = getattr(new, field_name)
            if kind == "cent":
                old_q: Decimal | None = round_cent(Decimal(old_val)) if old_val is not None else None
                new_q: Decimal | None = round_cent(Decimal(new_val)) if new_val is not None else None
                if old_q != new_q:
                    changes.append(f"[{ts}] {label} {_fmt_remark_value(old_q)}→{_fmt_remark_value(new_q)}；")
                    data[field_name] = new_q
            elif kind == "weight":
                old_w = round_weight(Decimal(old_val)) if old_val is not None else None
                new_w = round_weight(Decimal(new_val)) if new_val is not None else None
                if old_w != new_w:
                    changes.append(f"[{ts}] {label} {_fmt_remark_value(old_w)}→{_fmt_remark_value(new_w)}；")
                    data[field_name] = new_w
            elif kind == "qty":
                old_n: int = old_val or 0
                new_n: int = new_val or 0
                if old_n != new_n:
                    changes.append(f"[{ts}] {label} {old_n}→{new_n}；")
                    data[field_name] = new_n
            elif kind == "dist":
                # 新值为 None = 这份文件根本没有「订单标签」列 → 这一列不参与更新（ADR-0036 / AC15）。
                # 少了这条，一次不带标签的普通更新会把之前刷对的分销标记全部静默冲回 0。
                if new_val is None:
                    continue
                if (old_val or 0) != new_val:
                    changes.append(f"[{ts}] {label} {old_val or 0}→{new_val}；")
                    data[field_name] = new_val
            else:
                old_s = old_val or ""
                new_s = new_val or ""
                if old_s != new_s:
                    changes.append(f"[{ts}] {label} {_fmt_remark_value(old_s)}→{_fmt_remark_value(new_s)}；")
                    data[field_name] = new_val
        return changes, data

    async def _recompute_order_aggregates(
        self, order: OrderModel, raw: RawOrderRecord, extra: dict[str, Any] | None = None,
    ) -> None:
        """更新应用后按导入聚合口径重算主订单（Q6=A）：金额=子订单求和，退款状态推导。

        求和对象是已整到分的子订单行（钱走四舍五入、算出来的成本走见位进分，两把尺都只产出分精度值），
        所以主订单与行之和逐字相等（§15.1）。
        运费收入子订单级不落库，直接取新数据的订单级汇总（与导入同一来源口径）。
        extra 传入本次要一并写回的主订单级变更（order_status 覆盖、备注合并结果）。
        """
        details: Sequence[OrderDetailModel] = await OrderDetailCRUD(self.auth, self.db).get_list(
            search={"order_id": ("eq", order.id)},
        )
        buyer_paid = sum((d.paid_amount for d in details), Decimal("0"))
        refund = sum((d.refund_amount for d in details), Decimal("0"))
        shipped = sum((d.computed_product_cost for d in details), Decimal("0"))
        returned = sum((d.computed_return_cost for d in details), Decimal("0"))
        data: dict[str, Any] = {
            "buyer_paid_amount": buyer_paid,
            "refund_amount": refund,
            "refund_status": _derive_refund_status(buyer_paid, refund),
            "shipped_cost_amount": shipped,
            "return_cost_amount": returned,
            "freight_income": raw.freight_income,
        }
        if extra:
            data.update(extra)
        await OrderCRUD(self.auth, self.db).update(id=order.id, data=data)

    @staticmethod
    def _merge_remark(old: str | None, new: str | None) -> str | None:
        """备注合并（Q42，AN 轮改判）：返回需要落库的新值，无需变动返回 None。

        库里是历次 Excel 值用 `；` 拼出来的历史并集，Excel 那格是没拼过的原值，所以「库包含文件」
        是常态。判据按 `；` 切段后比集合，不比裸子串——裸子串两个方向都会出错：`old in new` 会把
        库里独立的一小句（如「已」）当成已覆盖而丢掉，缺 `new in old` 那条则让同一份文件反复导入
        拼出 `A；B；B；B`，违反幂等不变式。切段两边都 trim、空段丢弃，其余原文不动。
        """
        old_segs = _remark_segments(old)
        new_segs = _remark_segments(new)
        if not new_segs:
            return None
        if not old_segs:
            return new
        if set(new_segs) <= set(old_segs):
            return None
        if set(old_segs) <= set(new_segs):
            return new
        missing = [seg for seg in new_segs if seg not in set(old_segs)]
        return f"{old}；{'；'.join(missing)}"

    async def missing_cost_list(
        self,
        page_no: int,
        page_size: int,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[OrderDetailOutSchema]:
        """缺失成本（待补充）清单：子订单 cost_missing=true（ADR-0023）。"""
        offset = (page_no - 1) * page_size
        return await OrderDetailCRUD(self.auth, self.db).page(
            offset=offset,
            limit=page_size,
            order_by=order_by or [{"id": "asc"}],
            search={"cost_missing": ("eq", True)},
            out_schema=OrderDetailOutSchema,
        )

    async def reconcile_scam_orders(
        self,
        *,
        ship_month: str,
        approval_details: list[ScamApprovalDetailSchema],
    ) -> list[ScamReconciliationResultSchema]:
        """按发货月拉取标注刷单的订单，与钉钉销量权重审批明细逐笔核对（ADR-0028）。

        注意：刷单标记逻辑待需求方最终确认（§5.1 第10点）；发货时间已下沉到子订单级，
        故按子订单 ship_time 所属月份筛选主订单。

        参数:
        - ship_month (str): 发货月份(YYYY-MM)
        - approval_details (list[ScamApprovalDetailSchema]): 本月销量权重审批明细

        返回:
        - list[ScamReconciliationResultSchema]: 每笔刷单订单的核对结果
        """
        orders: Sequence[OrderModel] = await OrderCRUD(self.auth, self.db).get_list(
            search={"is_shua_order": ("eq", True)}, preload=["details"],
        )
        filtered = [o for o in orders if self._order_in_ship_month(o, ship_month)]
        return reconcile_orders(filtered, approval_details)

    @staticmethod
    def _order_in_ship_month(order: OrderModel, ship_month: str) -> bool:
        """主订单是否有任一子订单的发货时间落在指定月份(YYYY-MM)。"""
        for d in order.details:
            if d.ship_time and d.ship_time.strftime("%Y-%m") == ship_month:
                return True
        return False


class SkuCostService:
    """商品成本主表服务层（ADR-0022：唯一事实来源）"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        self.auth = auth
        self.db = db

    async def detail(self, id: int) -> SkuCostOutSchema:
        obj = await SkuCostCRUD(self.auth, self.db).get(id=id)
        if not obj:
            raise CustomException(msg="该商品成本记录不存在")
        return SkuCostOutSchema.model_validate(obj)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: SkuCostQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[SkuCostOutSchema]:
        offset = (page_no - 1) * page_size
        search_dict: dict[str, Any] = search_to_dict(search, {}) or {}
        cost_missing = search_dict.pop("cost_missing", None)
        if cost_missing is True:
            search_dict["cost_price"] = ("None", None)
        elif cost_missing is False:
            search_dict["cost_price"] = ("not None", None)
        return await SkuCostCRUD(self.auth, self.db).page(
            offset=offset,
            limit=page_size,
            order_by=order_by or [{"id": "asc"}],
            search=search_dict,
            out_schema=SkuCostOutSchema,
        )

    async def create(self, data: SkuCostCreateSchema) -> SkuCostOutSchema:
        crud = SkuCostCRUD(self.auth, self.db)
        # 成本价只准写到分（2026-11-02 AN13 改判 / AN16「不用舍、进位，只需要报错」）：填出小数第 3 位
        # 说明录的人自己还没想清楚，系统替他进位等于悄悄改账——拒收，并把收到的那个数报回去。
        if data.cost_price is not None and over_cent(data.cost_price):
            raise CustomException(msg=f"创建失败，成本价最多写到分（2 位小数），收到 {data.cost_price}，请改成正确的成本价")
        main_sku_code = normalize_merchant_code(data.main_sku_code)
        # 归一化会把「   」变成 None，而框架 `_build_conditions` 是「值为 None 的条件整个丢掉」：
        # 下面那几句查重就会从「这个主编码下有没有」退化成「随便哪个主编码下有没有」，
        # 既能把别人的记录算到这条头上，也能让 NULL 编码的行真的建出来（§16 A1）。
        if main_sku_code is None:
            raise CustomException(msg="创建失败，主商家编码不能为空（纯空格也算空）")
        sku_code = normalize_spec_code(main_sku_code, data.sku_code)
        # 无规格行不配拥有规格名称（§15.7 AA7）：既然不分规格，填了也没人能对得上，留着只会误导取价的人
        if sku_code is None and (data.sku_name or "").strip():
            raise CustomException(msg="该商品不分规格（规格商家编码为空），规格名称必须留空")
        if sku_code is None:
            if await crud.get(main_sku_code=main_sku_code, sku_code=("None", None)):
                raise CustomException(msg="创建失败，该主商家编码已有「不分规格」的成本记录，请直接修改那条商品级记录")
            # 混挂双向禁死（§5.1 第7步）：主编码下已有规格行时再建商品级行，
            # 那条商品级行永远取不到（兜底闸会拦住它），只能变成待补充清单里的一条谜案。
            if await crud.get(main_sku_code=main_sku_code, sku_code=("not None", None)):
                raise CustomException(
                    msg=f"创建失败，主商家编码 {main_sku_code} 已有按规格录入的成本记录，"
                    "不能再建「不分规格」的商品级记录；若这个商品确实不分规格，请先删掉那些规格级记录"
                )
        else:
            if await crud.get(sku_code=sku_code):
                raise CustomException(msg="创建失败，该规格商家编码已存在成本记录")
            # 反方向同样禁死：商品级行已代表「这个商品不分规格」，就不该再有规格级行
            if await crud.get(main_sku_code=main_sku_code, sku_code=("None", None)):
                raise CustomException(
                    msg=f"创建失败，主商家编码 {main_sku_code} 已登记为「不分规格」的商品级成本记录，"
                    "不能再按规格单独录成本；若这个商品其实分规格，请先删掉那条商品级记录"
                )
        obj = await crud.create(
            data=data.model_copy(
                update={"main_sku_code": main_sku_code, "sku_code": sku_code,
                        "sku_name": None if sku_code is None else data.sku_name},
            )
        )
        return SkuCostOutSchema.model_validate(obj)

    async def update(self, id: int, data: SkuCostUpdateSchema) -> SkuCostOutSchema:
        crud = SkuCostCRUD(self.auth, self.db)
        obj: SkuCostModel | None = await crud.get(id=id)
        if not obj:
            raise CustomException(msg="更新失败，该商品成本记录不存在")
        # 必须传 dict：CRUDBase.update 收到 Pydantic 模型会 exclude_none 丢弃显式 null，
        # 而「成本价留空=缺失」(ADR-0023) 正是靠显式 null 表达的——清空成本价会静默不生效。
        update_data = data.model_dump(exclude_unset=True, exclude={"id"})
        # 代价是 NOT NULL 的两列也会收到显式 null（界面走必填校验挡不住，直连接口的调用方挡不住）。
        # 这两列的 null 没有可落库的含义，按框架一贯的 PATCH 语义忽略，别把 IntegrityError 抛给调用方。
        for not_null_col in ("product_name", "cost_source"):
            if update_data.get(not_null_col) is None:
                update_data.pop(not_null_col, None)
        # 新增那道精度闸这里照样过（AN13）：`exclude_unset` 让「这次没动成本价」根本不在 dict 里，
        # 只有真改了才校验；显式 null=清空成本(ADR-0023)，不是超位。
        edited_cost = update_data.get("cost_price")
        if edited_cost is not None and over_cent(edited_cost):
            raise CustomException(msg=f"更新失败，成本价最多写到分（2 位小数），收到 {edited_cost}，请改成正确的成本价")
        # 无规格行的规格名称锁死（§15.7 AA7）：只锁 sku_code 为 NULL 这一类，有规格行照常可改
        if obj.sku_code is None and (update_data.get("sku_name") or "").strip():
            raise CustomException(msg="该商品不分规格（规格商家编码为空），规格名称必须留空")
        if update_data:
            obj = await crud.update(id=id, data=update_data)
        return SkuCostOutSchema.model_validate(obj)

    async def delete(self, ids: list[int]) -> None:
        if not ids:
            raise CustomException(msg="删除失败，删除对象不能为空")
        objs: Sequence[SkuCostModel] = await SkuCostCRUD(self.auth, self.db).get_list(search={"id": ("in", ids)})
        obj_map = {o.id: o for o in objs}
        for id_ in ids:
            if id_ not in obj_map:
                raise CustomException(msg="删除失败，该商品成本记录不存在")
        # 物理删除：sku_code 上是唯一索引，软删的幽灵行仍占着编码，删掉后同一个编码再也录不回来。
        await self.db.execute(delete(SkuCostModel).where(SkuCostModel.id.in_(ids)))
        await self.db.flush()
