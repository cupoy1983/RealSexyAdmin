"""订单数据源可插拔接口（ADR-0027）。

第一期默认实现是快麦 ERP 导出的 Excel（``excel_source.py``）；API 通路
（``kuaimai_api_source.py``）同样实现本接口但不作为默认数据源，待快麦 API
通路稳定后可切换，不改动下游成本/利润逻辑。

快麦导出的每一行是一个「订单商品明细」（子订单），多行可共享同一个「系统订单号」；
数据源负责按系统订单号归并为主订单 + 子订单列表（ADR-0030），金额聚合在数据源内完成，
成本相关聚合（依赖系统成本主表）留给 service 层。
"""

from dataclasses import dataclass, field
from decimal import Decimal


def normalize_merchant_code(value: str | None) -> str | None:
    """商家编码统一去空格 + 转大写，空串归一成 NULL（2026-10-25 口径）。

    数据库那把尺子本来就不分大小写（取价、查重、搜索都不受影响），只有下面「两码相同=不分规格」
    这句判断是我们自己逐字符比对的。编码不统一成一种写法，这句就会把 `SM037` / `sm037` 判成
    两个规格，明明不分规格的商品被当成规格级去取价、取不到就报缺失。
    """
    stripped = value.strip() if value else ""
    return stripped.upper() or None


def normalize_spec_code(main_sku_code: str | None, sku_code: str | None) -> str | None:
    """两码相同即「该商品不分规格」，规格商家编码统一落 NULL（ADR-0023 修订块）。

    快麦对无规格商品就是把主商家编码重复填进规格商家编码，所以这条归一化在落库前必须做。
    比对与落库前两个编码都先按 ``normalize_merchant_code`` 整形，只差大小写的重复填写也算不分规格。
    """
    spec = normalize_merchant_code(sku_code)
    if spec is None or spec == normalize_merchant_code(main_sku_code):
        return None
    return spec


# 分销订单分类值（ADR-0036）：0=非分销、1=代拍分销，2/3/4 预留给以后按更多分类分支走的口径。
# 明确不是布尔——业务方要求留分类位。
DISTRIBUTION_TYPE_NORMAL = 0
DISTRIBUTION_TYPE_PROXY = 1
# 判据只有这一条触发词。快麦标签里的「淘宝/天猫/抖音/京东分销订单」说的是**销售渠道**，
# 跟谁发货无关（实测一份导出里含「分销订单」47 行、其中 44 行是我们自己圆通单号发的货），
# 只有 `已生成采购单` 才是"货是 1688 商家那边发的"。
DISTRIBUTION_TAG_TRIGGER = "已生成采购单"


def classify_distribution_order(tag: str | None) -> int:
    """Excel「订单标签」→ 分销订单分类值（ADR-0036，2026-10-28 决议 AC16/AC23）。

    标签是逗号拼的多值串（`拼多多分销订单,已生成采购单`），所以只做**归一后的子串包含**判断：
    归一 = 去掉全部空格（含全角空格），不做全等比较、不看其它字样。
    """
    normalized = "".join((tag or "").split())
    return DISTRIBUTION_TYPE_PROXY if DISTRIBUTION_TAG_TRIGGER in normalized else DISTRIBUTION_TYPE_NORMAL


@dataclass
class RawOrderDetailRecord:
    """数据源返回的子订单（一行订单商品明细）中间表示（非 ORM，供 service 层落库前统一处理）"""

    source_detail_id: str
    sku_code: str | None
    product_name: str
    platform_order_no: str | None = None
    order_type: str | None = None
    detail_status: str | None = None
    main_sku_code: str | None = None
    sku_name: str | None = None
    weight_kg: Decimal | None = None
    ship_qty: int = 0
    return_qty: int = 0
    paid_amount: Decimal = Decimal("0")
    refund_amount: Decimal = Decimal("0")
    freight_income: Decimal = Decimal("0")  # 仅用于汇总到主订单，不落子订单表
    warehouse_name: str | None = None
    courier_template: str | None = None
    tracking_no: str | None = None
    is_erp_shipped: bool = False
    # 分销订单分类（ADR-0036）：0/1 是判据算出来的值，**None 表示这份文件根本没有「订单标签」列**
    # ——更新路径靠这个区分「按新标签覆盖」与「这一列不参与更新」（2026-10-28 决议 AC15）。
    distribution_order_type: int | None = None
    pay_time: str | None = None
    ship_time: str | None = None
    finish_time: str | None = None
    after_sales_no: str | None = None
    excel_cost_price: Decimal | None = None
    erp_shipped_cost: Decimal | None = None
    erp_return_cost: Decimal | None = None


@dataclass
class RawOrderRecord:
    """数据源返回的主订单中间表示（非 ORM，供 service 层落库前统一处理）

    店铺由 ``kuaimai_shop_id``（Excel「店铺编码」）+ ``store_name``（店铺全称）+
    ``platform``（店铺平台）自动建档（ADR-0024），因此不再有「未映射店铺/挂起」。
    ``buyer_paid_amount``/``refund_amount``/``freight_income`` 是子订单求和；成本聚合
    （``shipped_cost_amount``/``return_cost_amount``）依赖系统成本主表，由 service 层计算。
    """

    system_order_no: str
    kuaimai_shop_id: str
    store_name: str
    platform: str
    order_status: str
    buyer_paid_amount: Decimal = Decimal("0")
    refund_amount: Decimal = Decimal("0")
    freight_income: Decimal = Decimal("0")
    province: str | None = None
    city: str | None = None
    system_remark: str | None = None  # 归并时取第一行子订单的值（ADR-0031）
    seller_remark: str | None = None  # 归并时取第一行子订单的值（ADR-0031）
    is_shua_order: bool = False
    details: list[RawOrderDetailRecord] = field(default_factory=list)


class OrderDataSource:
    """订单数据源抽象接口。

    子类须实现 ``fetch``，返回统一的 ``RawOrderRecord`` 列表，供
    ``order.service.OrderService`` 落库——数据源的切换不影响落库/成本计算逻辑。
    """

    async def fetch(self, **params: object) -> list[RawOrderRecord]:
        """拉取订单原始记录。

        参数:
        - params: 数据源特定的拉取参数（Excel 实现接收文件内容，API 实现接收时间范围等）

        返回:
        - list[RawOrderRecord]: 统一中间表示的订单记录列表
        """
        raise NotImplementedError
