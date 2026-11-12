"""导入批次服务层（ADR-0033）。

批次 = 一次 Excel 导入形成的数据集合，是人工确认的单位、也是利润的准入门槛。
本模块只提供批次本身的读写与门禁判定；**确认动作**分别落在自己的子域：
账单批次确认（含运费覆盖式回写）在 `logistics.service`，订单批次确认在 `order.service`。
这样 common 不反向依赖任何业务子域，依赖图保持单向。
"""

import re
from collections.abc import Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.exceptions import CustomException
from app.utils.common_util import search_to_dict

from .crud import ImportBatchCRUD
from .model import (
    BATCH_STATUS_CONFIRMED,
    BATCH_STATUS_UNCONFIRMED,
    FILE_CATEGORY_BILL,
    ImportBatchModel,
    build_batch_no,
)
from .schema import BillGateGapSchema, ImportBatchOutSchema, ImportBatchQueryParam

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

# 基准月固定东八区：开发机是 Windows、只有容器里显式设了 TZ，本机时钟一飘，
# 「拒绝未来月」和前端那份窗口就会跟着飘。窗口本身只在界面拦（ADR-0033），接口只校验格式与未来月。
BUSINESS_TZ = ZoneInfo("Asia/Shanghai")

# 空文件在三个导入入口（订单导入 / 订单更新 / 账单导入）说同一句话（设计文档 §5.2）：
# 0 字节上传、空工作表、只有表头没有数据行，是同一种「没读到数据」，不该让人猜哪一种出了别的毛病。
EMPTY_IMPORT_FILE_MSG = "上传文件为空：没有读到任何数据行，请确认导出的 Excel 里有数据"


def _current_month() -> str:
    return datetime.now(BUSINESS_TZ).strftime("%Y-%m")


def validate_month(month: str, *, field_name: str = "月份") -> str:
    """校验 "YYYY-MM" 格式并拒绝未来月份；两类导入与空批次声明都用这条。"""
    if not month or not _MONTH_RE.match(month.strip()):
        raise CustomException(msg=f"{field_name}格式错误，应为 YYYY-MM（如 2026-09），收到：{month!r}")
    normalized = month.strip()
    if normalized > _current_month():
        raise CustomException(msg=f"{field_name}不能选择未来月份：{normalized} 晚于 {_current_month()}")
    return normalized


class ImportBatchService:
    """导入批次：建档、查询、确认门禁判定"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        self.auth = auth
        self.db = db

    @property
    def _crud(self) -> ImportBatchCRUD:
        return ImportBatchCRUD(self.auth, self.db)

    # ── 查询 ────────────────────────────────────────────────────────────

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: ImportBatchQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[ImportBatchOutSchema]:
        offset = (page_no - 1) * page_size
        return await self._crud.page(
            offset=offset,
            limit=page_size,
            order_by=order_by or [{"batch_month": "desc"}, {"id": "desc"}],
            search=search_to_dict(search, {}),
            out_schema=ImportBatchOutSchema,
        )

    async def detail(self, id: int) -> ImportBatchOutSchema:
        obj = await self._crud.get_or_404(id=id, msg="该导入批次不存在")
        return ImportBatchOutSchema.model_validate(obj)

    async def get_by_batch_no(self, batch_no: str) -> ImportBatchModel | None:
        return await self._crud.get(batch_no=batch_no)

    async def list_for_month(self, *, file_category: str, batch_month: str) -> Sequence[ImportBatchModel]:
        return await self._crud.get_list(search={"file_category": file_category, "batch_month": batch_month})

    # ── 建档 ────────────────────────────────────────────────────────────

    async def create_batch(
        self, *, file_category: str, batch_month: str, sub_category: str | None = None,
        row_count: int = 0, order_count: int = 0,
    ) -> ImportBatchModel:
        """导入建档：同批次号已存在则拒绝（一个文件类别+子类别+月份只允许一个批次）。"""
        batch_no = build_batch_no(file_category, batch_month, sub_category)
        if await self._crud.get(batch_no=batch_no):
            label = "账单" if file_category == FILE_CATEGORY_BILL else "订单"
            raise CustomException(msg=f"导入失败：{batch_month} 的{label}批次（{batch_no}）已存在，同月同类型只允许导入一次")
        return await self._crud.create(
            data={
                "batch_no": batch_no,
                "file_category": file_category,
                "sub_category": sub_category,
                "batch_month": batch_month,
                "status": BATCH_STATUS_UNCONFIRMED,
                "row_count": row_count,
                "order_count": order_count,
            }
        )

    # ── 门禁 ────────────────────────────────────────────────────────────

    async def require_unconfirmed(self, batch_no: str) -> ImportBatchModel:
        """取一条必须存在且未确认的批次（订单更新的前置校验，ADR-0032「更新不新建批次」）。

        批次不存在也拒绝：自动补建等于绕开可导入月份窗口给任意月份开批次。
        """
        obj = await self._crud.get(batch_no=batch_no)
        if not obj:
            raise CustomException(msg=f"批次 {batch_no} 不存在，请先用「导入订单」建该月批次再做更新")
        if obj.status == BATCH_STATUS_CONFIRMED:
            raise CustomException(msg=f"批次 {batch_no} 已确认，不允许再更新；如需订正只能直接修改数据库")
        return obj

    async def confirmed_batch_nos(self, batch_nos: set[str]) -> set[str]:
        """给定批次号里已确认的那一部分。

        运费摊分只认已确认批次的原件行：未确认批次的账单还只是原始数据，不允许提前进成本。
        """
        if not batch_nos:
            return set()
        rows: Sequence[ImportBatchModel] = await self._crud.get_list(
            search={"batch_no": ("in", sorted(batch_nos)), "status": BATCH_STATUS_CONFIRMED},
        )
        return {r.batch_no for r in rows}

    async def bill_gate_gaps(self, bill_month: str, *, active_bill_types: Sequence[str]) -> BillGateGapSchema:
        """该月账单的缺口清单（ADR-0033 门禁 + 界面预检共用一处）。

        「有批次」包括人工声明的零行批次——它同样是这个月账单齐不齐的一条事实。
        启用中的解析规则为 0 条单列成一个缺口（`no_active_rules`）：它不等于「这个月不需要账单」，
        两 lists 都空着放行过就是缺陷本身（§15.6 Z5）。
        """
        batches: Sequence[ImportBatchModel] = await self._crud.get_list(
            search={"file_category": FILE_CATEGORY_BILL, "batch_month": bill_month},
        )
        return BillGateGapSchema(
            bill_month=bill_month,
            unconfirmed_batches=sorted(b.batch_no for b in batches if b.status == BATCH_STATUS_UNCONFIRMED),
            missing_types=sorted(set(active_bill_types) - {b.sub_category for b in batches if b.sub_category}),
            no_active_rules=not active_bill_types,
        )

    async def assert_bill_batches_confirmed(self, bill_month: str, *, active_bill_types: Sequence[str]) -> None:
        """订单批次确认门禁：该月每个启用中的账单类型都必须有**已确认**的账单批次（ADR-0033）。

        启用中的解析规则为 0 条同样拒绝（§15.6 Z5）——「没人配置过怎么读账单」与「这个月不需要账单」
        是两件事，按空集合放行等于让利润认一批从来没被核对过的数据。

        参数:
        - bill_month (str): 订单批次所属月份，账单按同月比对（严格同月）
        - active_bill_types (Sequence[str]): 启用中的账单类型编码。由调用方传入——common 不反向
          依赖 logistics，否则 `logistics.service → common.service` 会成环。
        """
        if not active_bill_types:
            raise CustomException(
                msg=f"确认失败：当前没有任何启用中的账单解析规则，{bill_month} 的订单批次不能确认。"
                    "请先到「快递账单 → 解析规则」建档或启用；确实一家快递都不用的，也该有一条启用中的规则兜着。"
            )
        gaps = await self.bill_gate_gaps(bill_month, active_bill_types=active_bill_types)
        if gaps.passed:
            return
        parts: list[str] = []
        if gaps.unconfirmed_batches:
            parts.append(f"未确认的账单批次：{'、'.join(gaps.unconfirmed_batches)}")
        if gaps.missing_types:
            parts.append(f"本月没有账单批次的快递类型：{'、'.join(gaps.missing_types)}（确实无账单请先声明）")
        raise CustomException(msg=f"确认失败：{bill_month} 的账单不齐——{'；'.join(parts)}。门禁顺序是先确认账单，再确认订单。")

    async def mark_confirmed(self, batch: ImportBatchModel) -> ImportBatchModel:
        batch.status = BATCH_STATUS_CONFIRMED
        batch.confirmed_time = datetime.now()
        await self.db.flush()
        await self.db.refresh(batch)
        return batch

    async def add_counts(self, batch: ImportBatchModel, *, row_count: int = 0, order_count: int = 0) -> ImportBatchModel:
        """累加导入统计（订单更新新建的订单也计入其归属批次）。"""
        batch.row_count += row_count
        batch.order_count += order_count
        await self.db.flush()
        await self.db.refresh(batch)
        return batch

    async def append_remark(self, batch: ImportBatchModel, text: str) -> ImportBatchModel:
        """提示只往后拼、从不删旧内容（同账单行的异常原因口径）。"""
        batch.remark = f"{batch.remark}{text}" if batch.remark else text
        await self.db.flush()
        await self.db.refresh(batch)
        return batch
