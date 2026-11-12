from typing import Any

from sqlalchemy import exists, false, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from .importers.base import DISTRIBUTION_TYPE_NORMAL
from .model import OrderDetailModel, OrderModel, SkuCostModel
from .schema import (
    OrderDetailCreateSchema,
    SkuCostCreateSchema,
    SkuCostUpdateSchema,
)


def missing_bill_conditions() -> list[ColumnElement]:
    """缺账单判据——这条尺子只有这一个住处（§24，AK 轮）。

    ERP发货 + 运单号非空 + 运费成本还是 NULL + 非分销（最后一条随 ADR-0036：分销行的运单号
    是 1688 商家那边承运商的，永远不进我们的快递账单，留着就常年混着"永远不来"的行）。
    主订单列表的 EXISTS 与批次确认前的缺账单计数都套它，两处不许各写一遍。
    """
    return [
        OrderDetailModel.is_erp_shipped == true(),
        OrderDetailModel.tracking_no.isnot(None),
        OrderDetailModel.freight_cost.is_(None),
        OrderDetailModel.distribution_order_type == DISTRIBUTION_TYPE_NORMAL,
    ]


class OrderCRUD(CRUDBase[OrderModel, dict, dict]):
    """订单头 CRUD（创建/更新走导入批处理内部字典，不走标准 Create/UpdateSchema）"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=OrderModel, auth=auth, db=db)

    async def _build_conditions(self, include_deleted: bool = False, **kwargs: Any) -> list[ColumnElement]:
        """拦截非模型列的查询键：update_status / ship_time / freight_missing 转 EXISTS 子订单子查询（ADR-0032、§24）。"""
        update_status = kwargs.pop("update_status", None)
        ship_time = kwargs.pop("ship_time", None)
        freight_missing = kwargs.pop("freight_missing", None)
        conditions = await super()._build_conditions(include_deleted=include_deleted, **kwargs)

        def _detail_exists(*extra: ColumnElement) -> ColumnElement:
            return exists(
                select(OrderDetailModel.id).where(
                    OrderDetailModel.order_id == OrderModel.id,
                    OrderDetailModel.is_deleted == false(),
                    *extra,
                )
            )

        if update_status == "updated":
            conditions.append(
                _detail_exists(
                    OrderDetailModel.update_remark.isnot(None),
                    OrderDetailModel.update_remark != "",
                )
            )
        elif update_status == "not_updated":
            conditions.append(
                ~_detail_exists(
                    OrderDetailModel.update_remark.isnot(None),
                    OrderDetailModel.update_remark != "",
                )
            )

        if isinstance(ship_time, tuple) and ship_time[0] == "between":
            val = ship_time[1]
            if isinstance(val, (list, tuple)) and len(val) == 2:
                conditions.append(_detail_exists(OrderDetailModel.ship_time.between(val[0], val[1])))

        # 命中 = 这张主订单下至少有一条缺账单子订单；EXISTS 不是 JOIN，一张单多条缺也不重复出行
        if freight_missing is True:
            conditions.append(_detail_exists(*missing_bill_conditions()))

        return conditions


class OrderDetailCRUD(CRUDBase[OrderDetailModel, OrderDetailCreateSchema, dict]):
    """订单明细 CRUD"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=OrderDetailModel, auth=auth, db=db)

    async def _build_conditions(self, include_deleted: bool = False, **kwargs: Any) -> list[ColumnElement]:
        """拦截非模型列的查询键：`missing_bill=True` 即套用上面那把唯一的缺账单尺子。"""
        missing_bill = kwargs.pop("missing_bill", None)
        conditions = await super()._build_conditions(include_deleted=include_deleted, **kwargs)
        if missing_bill is True:
            conditions.extend(missing_bill_conditions())
        return conditions


class SkuCostCRUD(CRUDBase[SkuCostModel, SkuCostCreateSchema, SkuCostUpdateSchema]):
    """商品成本主表 CRUD"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=SkuCostModel, auth=auth, db=db)
