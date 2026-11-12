"""快麦 ERP API 订单数据源（备用通路，不作为默认数据源，ADR-0027）。

第一期默认数据源仍是 Excel 导出（见 ``excel_source.py``）；本实现预留给 API
通路稳定后切换使用，实现同一 ``OrderDataSource`` 接口，不改动下游落库/成本逻辑。

钱那一类格子照旧走 `common/numbers.py::round_cent`（ADR-0039）：这一条路现在 `fetch` 必抛、
还没接线，但不写就有这么一天——同一条列由两个数据源填，只有一侧量化就等于凭空多出第二把尺。
"""

from decimal import Decimal
from typing import Any

from redis.asyncio.client import Redis

from app.core.exceptions import CustomException
from app.plugin.infra.integration.kuaimai_client import KuaimaiClient
from app.plugin.module_ecommerce.common.numbers import round_cent

from .base import OrderDataSource, RawOrderDetailRecord, RawOrderRecord, normalize_merchant_code, normalize_spec_code


class KuaimaiApiOrderDataSource(OrderDataSource):
    """快麦 ERP API 订单数据源（非默认，§7/ADR-0027）。

    具体订单拉取接口字段以快麦官方开放平台文档为准；此处预留最小骨架，
    真实接入时在 ``KuaimaiClient`` 补充对应方法后在此处调用并转换为
    ``RawOrderRecord``，不改变 ``OrderDataSource`` 对外契约。
    """

    def __init__(self, redis: Redis) -> None:
        self.client = KuaimaiClient(redis)

    async def fetch(self, **params: object) -> list[RawOrderRecord]:
        """按参数拉取订单（预留骨架，第一期不作为默认数据源，不接入实际业务）。

        参数:
        - params: 快麦订单拉取参数（如时间范围），字段以快麦官方文档为准

        返回:
        - list[RawOrderRecord]: 转换后的订单记录列表
        """
        raise CustomException(msg="快麦 API 订单数据源第一期尚未接入业务数据，请使用 Excel 导入（ADR-0027）")

    @staticmethod
    def _to_raw_order(raw: dict[str, Any]) -> RawOrderRecord:
        """将快麦 API 原始订单记录转换为统一中间表示（字段名以快麦官方文档为准，待接入时补全）。"""
        return RawOrderRecord(
            system_order_no=str(raw.get("system_order_no") or ""),
            kuaimai_shop_id=str(raw.get("kuaimai_shop_id") or ""),
            store_name=str(raw.get("store_name") or ""),
            platform=str(raw.get("platform") or ""),
            order_status=str(raw.get("order_status") or ""),
            buyer_paid_amount=round_cent(Decimal(str(raw.get("buyer_paid_amount") or "0"))),
            refund_amount=round_cent(Decimal(str(raw.get("refund_amount") or "0"))),
            freight_income=round_cent(Decimal(str(raw.get("freight_income") or "0"))),
            details=[
                RawOrderDetailRecord(
                    source_detail_id=str(d.get("source_detail_id") or ""),
                    sku_code=normalize_spec_code(d.get("main_sku_code"), d.get("sku_code")),
                    main_sku_code=normalize_merchant_code(d.get("main_sku_code")),
                    product_name=str(d.get("product_name") or ""),
                    platform_order_no=d.get("platform_order_no"),
                    ship_qty=int(d.get("ship_qty") or 0),
                    return_qty=int(d.get("return_qty") or 0),
                    paid_amount=round_cent(Decimal(str(d.get("paid_amount") or "0"))),
                    refund_amount=round_cent(Decimal(str(d.get("refund_amount") or "0"))),
                )
                for d in raw.get("details", [])
            ],
        )
