from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.orm.base import LoaderCallableStatus, instance_state

from app.core.base_model import ModelMixin, UserMixin


class OrderModel(ModelMixin, UserMixin):
    """主订单 - 按系统订单号归并子订单，金额字段为子订单求和（§4/§5.1，ADR-0030）"""

    __tablename__: str = "ecommerce_order"
    __table_args__: dict[str, str] = {"comment": "电商主订单"}

    system_order_no: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True, comment="系统订单号（归并键）")
    store_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ecommerce_store.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False, index=True, comment="店铺ID（导入时按快麦店铺id自动建档得到，ADR-0024）",
    )
    platform: Mapped[str] = mapped_column(String(64), nullable=False, comment="所属平台（店铺平台）")
    order_status: Mapped[str] = mapped_column(String(32), nullable=False, comment="订单状态（原始值，来自导入数据源）")
    buyer_paid_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False, comment="商品买家已付金额（= Σ子订单 paid_amount，含运费收入）")
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False, comment="退款金额（= Σ子订单 refund_amount）")
    refund_status: Mapped[str] = mapped_column(String(16), default="none", nullable=False, comment="退款状态(none:无退款 full:整单退款 partial:部分退款)")
    shipped_cost_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False, comment="发货成本（= Σ子订单 computed_product_cost，利润中减项）")
    return_cost_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False, comment="退货成本（= Σ子订单 computed_return_cost，利润中加回项）")
    freight_income: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False, comment="运费收入（= Σ子订单运费收入，仅统计展示，已含在买家已付里，不叠加进实际销售额）")
    freight_cost_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False, comment="运费成本求和（= Σ子订单 freight_cost，账单批次确认时覆盖式刷新）")
    province: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, comment="省份")
    city: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, comment="城市")
    system_remark: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="系统备注（导入归并时取第一行子订单的值，ADR-0031）")
    seller_remark: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="卖家备注（导入归并时取第一行子订单的值，ADR-0031）")
    is_shua_order: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True, comment="订单备注是否标注刷单（原始标记，未核对）")
    shua_status: Mapped[str] = mapped_column(
        String(16), default="not_shua", nullable=False, index=True,
        comment="刷单核对状态(not_shua:非刷单 pending:待核对 matched:核对成功 mismatch:核对失败/重大错误)",
    )
    data_source: Mapped[str] = mapped_column(String(16), default="excel", nullable=False, comment="数据来源(excel/api)，ADR-0027")
    import_batch_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
        comment="导入批次号（必填：一行数据必须挂在一个批次上，否则既确认不了也没人看得见，ADR-0033）",
    )

    store: Mapped["StoreModel"] = relationship("StoreModel", foreign_keys="OrderModel.store_id", uselist=False)  # noqa: F821
    details: Mapped[list["OrderDetailModel"]] = relationship(
        "OrderDetailModel", back_populates="order", cascade="all, delete-orphan",
    )

    @property
    def store_name(self) -> str | None:
        """店铺名称（供序列化用；仅在 store 关系已预加载时取值，避免异步懒加载）。"""
        loaded = instance_state(self).dict.get("store", LoaderCallableStatus.NEVER_SET)
        if loaded in (LoaderCallableStatus.NEVER_SET, LoaderCallableStatus.PASSIVE_NO_RESULT):
            return None
        return loaded.store_name if loaded is not None else None


class OrderDetailModel(ModelMixin, UserMixin):
    """子订单 - 一行「订单商品明细」；运单号/发货时间/平台订单号都在此级（§4，ADR-0025/0030）"""

    __tablename__: str = "ecommerce_order_detail"
    __table_args__: dict[str, str] = {"comment": "电商子订单（订单商品明细）"}

    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ecommerce_order.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False, index=True, comment="主订单ID",
    )
    source_detail_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True, comment="订单商品明细ID（数据源唯一键）")
    platform_order_no: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, index=True, comment="平台订单号")
    order_type: Mapped[str | None] = mapped_column(String(32), default=None, nullable=True, comment="订单类型（平台/线下/补发/空单/拆单等，一律计入收入，ADR-0030）")
    detail_status: Mapped[str | None] = mapped_column(String(32), default=None, nullable=True, comment="订单状态(明细级原始值)")

    sku_code: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, index=True, comment="规格商家编码(SKU)；NULL=该商品不分规格（与主商家编码相同一律归一为 NULL，ADR-0023）")
    main_sku_code: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, index=True, comment="主商家编码(款)")
    sku_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="规格属性名称(SKU)")
    product_name: Mapped[str] = mapped_column(String(255), nullable=False, comment="商品标题(款)")
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), default=None, nullable=True, comment="重量kg(SKU)")

    cost_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True, comment="落库时从商品成本主表取值；NULL=成本缺失(ADR-0023)。分销订单行不查主表，取Excel「成本价(SKU)」原值(ADR-0036)")
    ship_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="系统实发商品数")
    return_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="实退数量")
    computed_product_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False, comment="发货成本 =cost_price×ship_qty")
    computed_return_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False, comment="退货成本 =cost_price×return_qty")
    cost_missing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True, comment="cost_price 为 NULL 时为 true，进待补充清单")

    paid_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False, comment="订单买家已付金额（行级）")
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False, comment="平台实退金额（行级）")

    warehouse_name: Mapped[str | None] = mapped_column(String(128), default=None, nullable=True, comment="仓库名称")
    courier_template: Mapped[str | None] = mapped_column(String(128), default=None, nullable=True, comment="快递模版名称")
    tracking_no: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, index=True, comment="快递单号（账单匹配键，ADR-0025）")
    is_erp_shipped: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, comment="是否ERP发货（只有「ERP发货」为真，「其他ERP发货」「未发货」为假）")
    distribution_order_type: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False,
        comment="分销订单：0=非分销、1=代拍分销（Excel「订单标签」归一后含「已生成采购单」）、2/3/4 预留；成本与运费口径见 ADR-0036",
    )
    freight_cost: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), default=None, nullable=True,
        comment="运费成本：NULL=还没定（缺账单）、0=有意按0、>0=账单实额（账单批次确认时覆盖式写入，ADR-0034）",
    )

    pay_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="付款时间")
    ship_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True, comment="发货时间（运费归属月份的依据，ADR-0025）")
    finish_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="完成时间")

    after_sales_no: Mapped[str | None] = mapped_column(String(64), default=None, nullable=True, comment="售后工单号")

    excel_cost_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True, comment="成本价(SKU)快麦原值（仅比对告警，不覆盖 cost_price，ADR-0022）")
    erp_shipped_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="系统实发成本快麦原值（非分销行仅比对告警；分销行的 computed_product_cost 直接取此值，ADR-0022 例外见 ADR-0036）")
    erp_return_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="实退成本快麦原值（非分销行仅比对告警；分销行的 computed_return_cost 直接取此值，ADR-0022 例外见 ADR-0036）")

    update_remark: Mapped[str | None] = mapped_column(
        Text, default=None, nullable=True,
        comment="更新备注（订单更新变更日志，「[时间戳] 字段 旧值→新值；」逐批追加，ADR-0032）",
    )

    order: Mapped["OrderModel"] = relationship("OrderModel", back_populates="details")


class SkuCostModel(ModelMixin, UserMixin):
    """商品成本主表 - 唯一事实来源，不被导入数据覆盖（ADR-0022）"""

    __tablename__: str = "ecommerce_sku_cost"
    __table_args__: dict[str, str] = {"comment": "商品成本主表（唯一事实来源）"}

    sku_code: Mapped[str | None] = mapped_column(String(64), default=None, unique=True, nullable=True, index=True, comment="规格商家编码；NULL=不分规格的商品级成本行（唯一索引不约束 NULL 行）")
    main_sku_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True, comment="主商家编码(款)；商品级行的匹配键，同码只能有一条商品级行（服务层校验）")
    sku_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="规格名称")
    product_name: Mapped[str] = mapped_column(String(255), nullable=False, comment="商品名称")
    cost_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True, comment="单位采购成本（最多 2 位，ADR-0039 2026-11-06 收列）；NULL=缺失，0=有意0成本(ADR-0023)")
    cost_source: Mapped[str] = mapped_column(String(32), default="manual", nullable=False, comment="成本来源(manual:手工录入 import:批量导入)")
    remark: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="备注")
