from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.core.base_schema import BaseQueryParam, BaseSchema, UserByQueryParam, UserBySchema
from app.core.validator import DateTimeStr

# ── 订单明细(OrderDetail) ────────────────────────────────────────────────


class OrderDetailCreateSchema(BaseModel):
    """新增子订单（随订单导入批量创建，不单独对外暴露创建接口）"""

    source_detail_id: str = Field(..., min_length=1, max_length=64, description="订单商品明细ID")
    sku_code: str | None = Field(default=None, max_length=64, description="规格商家编码(SKU)；NULL=该商品不分规格(ADR-0023)")
    product_name: str = Field(..., min_length=1, max_length=255, description="商品标题(款)")
    ship_qty: int = Field(default=0, ge=0, description="系统实发商品数")
    return_qty: int = Field(default=0, ge=0, description="实退数量")


class OrderDetailOutSchema(BaseSchema, UserBySchema):
    """子订单响应模型"""

    model_config = ConfigDict(from_attributes=True)

    order_id: int = Field(description="主订单ID")
    source_detail_id: str = Field(description="订单商品明细ID")
    platform_order_no: str | None = Field(default=None, description="平台订单号")
    order_type: str | None = Field(default=None, description="订单类型")
    detail_status: str | None = Field(default=None, description="订单状态(明细)")
    sku_code: str | None = Field(default=None, description="规格商家编码(SKU)；NULL=该商品不分规格(ADR-0023)")
    main_sku_code: str | None = Field(default=None, description="主商家编码(款)")
    sku_name: str | None = Field(default=None, description="规格属性名称(SKU)")
    product_name: str = Field(description="商品标题(款)")
    weight_kg: Decimal | None = Field(default=None, description="重量kg(SKU)")
    cost_price: Decimal | None = Field(default=None, description="成本价（NULL=缺失）")
    ship_qty: int = Field(description="系统实发商品数")
    return_qty: int = Field(description="实退数量")
    computed_product_cost: Decimal = Field(description="发货成本")
    computed_return_cost: Decimal = Field(description="退货成本")
    cost_missing: bool = Field(description="成本是否缺失")
    paid_amount: Decimal = Field(description="订单买家已付金额（行级）")
    refund_amount: Decimal = Field(description="平台实退金额（行级）")
    freight_cost: Decimal | None = Field(default=None, description="运费成本：NULL=还没定(缺账单) 0=有意按0 >0=账单实额（ADR-0034）")
    warehouse_name: str | None = Field(default=None, description="仓库名称")
    courier_template: str | None = Field(default=None, description="快递模版名称")
    tracking_no: str | None = Field(default=None, description="快递单号")
    is_erp_shipped: bool = Field(description="是否ERP发货")
    distribution_order_type: int = Field(default=0, description="分销订单类型：0=非分销、1=代拍分销…（认领弹窗靠它灰化勾选框，ADR-0036）")
    pay_time: DateTimeStr | None = Field(default=None, description="付款时间")
    ship_time: DateTimeStr | None = Field(default=None, description="发货时间")
    finish_time: DateTimeStr | None = Field(default=None, description="完成时间")
    after_sales_no: str | None = Field(default=None, description="售后工单号")
    excel_cost_price: Decimal | None = Field(default=None, description="成本价(SKU)快麦原值（仅比对）")
    erp_shipped_cost: Decimal | None = Field(default=None, description="系统实发成本快麦原值（仅比对）")
    erp_return_cost: Decimal | None = Field(default=None, description="实退成本快麦原值（仅比对）")
    update_remark: str | None = Field(default=None, description="更新备注（订单更新变更日志，ADR-0032）")


class OrderDetailQueryParam(BaseQueryParam, UserByQueryParam):
    """子订单查询参数——「缺失成本待补充」清单走 cost_missing=true"""

    order_id: int | None = Field(None, description="主订单ID", json_schema_extra={"q": "eq"})
    source_detail_id: str | None = Field(None, description="订单商品明细ID", json_schema_extra={"q": "like"})
    platform_order_no: str | None = Field(None, description="平台订单号", json_schema_extra={"q": "like"})
    tracking_no: str | None = Field(None, description="快递单号", json_schema_extra={"q": "like"})
    sku_code: str | None = Field(None, description="规格商家编码", json_schema_extra={"q": "like"})
    product_name: str | None = Field(None, description="商品名称", json_schema_extra={"q": "like"})
    cost_missing: bool | None = Field(None, description="成本是否缺失", json_schema_extra={"q": "eq"})


# ── 订单(Order) ──────────────────────────────────────────────────────────


class OrderOutSchema(BaseSchema, UserBySchema):
    """主订单响应模型（子订单走 /order/detail/list/{order_id} 懒加载，不随本模型嵌套返回）"""

    model_config = ConfigDict(from_attributes=True)

    system_order_no: str = Field(description="系统订单号")
    store_id: int = Field(description="店铺ID")
    store_name: str | None = Field(default=None, description="店铺名称（来自店铺表关联）")
    platform: str = Field(description="所属平台")
    order_status: str = Field(description="订单状态")
    buyer_paid_amount: Decimal = Field(description="商品买家已付金额（子订单求和）")
    refund_amount: Decimal = Field(description="退款金额（子订单求和）")
    refund_status: str = Field(description="退款状态(none/full/partial)")
    shipped_cost_amount: Decimal = Field(description="发货成本（子订单求和）")
    return_cost_amount: Decimal = Field(description="退货成本（子订单求和）")
    freight_income: Decimal = Field(description="运费收入（仅统计，已含在买家已付里）")
    freight_cost_amount: Decimal = Field(description="运费成本求和（= Σ子订单 freight_cost，账单批次确认时覆盖式刷新，ADR-0034）")
    province: str | None = Field(default=None, description="省份")
    city: str | None = Field(default=None, description="城市")
    system_remark: str | None = Field(default=None, description="系统备注（导入首行，ADR-0031）")
    seller_remark: str | None = Field(default=None, description="卖家备注（导入首行，ADR-0031）")
    is_shua_order: bool = Field(description="是否标注刷单")
    shua_status: str = Field(description="刷单核对状态(not_shua/pending/matched/mismatch)")
    data_source: str = Field(description="数据来源(excel/api)")
    import_batch_id: str | None = Field(default=None, description="导入批次号")


class OrderQueryParam(BaseModel):
    """主订单查询参数

    不继承 BaseQueryParam/UserByQueryParam：订单搜索栏不提供创建/更新时间与创建人/更新人筛选，
    改为「更新状态」与「订单发货时间」范围（EXISTS 子订单，OrderCRUD 内转为子查询，ADR-0032）。
    「只看缺账单」同为一个 EXISTS 子订单的条件（判据见 crud.missing_bill_conditions，§24）。
    """

    system_order_no: str | None = Field(None, description="系统订单号", json_schema_extra={"q": "like"})
    store_id: int | None = Field(None, description="店铺ID", json_schema_extra={"q": "eq"})
    platform: str | None = Field(None, description="所属平台", json_schema_extra={"q": "eq"})
    refund_status: str | None = Field(None, description="退款状态", json_schema_extra={"q": "eq"})
    shua_status: str | None = Field(None, description="刷单核对状态", json_schema_extra={"q": "eq"})
    province: str | None = Field(None, description="省份", json_schema_extra={"q": "like"})
    city: str | None = Field(None, description="城市", json_schema_extra={"q": "like"})
    update_status: str | None = Field(None, description="更新状态(updated:有更新 not_updated:无更新)")
    ship_time: list[DateTimeStr] | None = Field(None, description="订单发货时间范围（EXISTS 子订单 ship_time）")
    freight_missing: bool | None = Field(
        None,
        description="只看缺账单：这张主订单下至少有一条「ERP发货 + 有运单号 + 运费成本还没定 + 非分销」的子订单（§24）",
    )


# ── 商品成本主表(SkuCost) ────────────────────────────────────────────────


class SkuCostCreateSchema(BaseModel):
    """新增商品成本：主商家编码必填；规格商家编码留空（或与主商家编码相同）即按「不分规格」登记（ADR-0023 修订块）"""

    main_sku_code: str = Field(..., min_length=1, max_length=64, description="主商家编码")
    sku_code: str | None = Field(default=None, max_length=64, description="规格商家编码；NULL=该商品不分规格")
    sku_name: str | None = Field(default=None, max_length=128, description="规格名称")
    product_name: str = Field(..., min_length=1, max_length=255, description="商品名称")
    # 只准到分（2026-11-02 AN13 改判 + AN16「不用舍、进位，只需要报错」）：闸立在 service 层，
    # 为的是报错说得出「收到多少、该怎么改」；落库列 2026-11-06 同轮收成 Numeric(12, 2)（AN24 那批已做完）。
    cost_price: Decimal | None = Field(
        default=None, ge=0,
        description="单位采购成本（最多 2 位小数）；留空=缺失，0=有意0成本(ADR-0023)；负数没有业务含义，一律拒绝(§15.4)",
    )
    cost_source: str = Field(default="manual", description="成本来源(manual/import)")
    remark: str | None = Field(default=None, max_length=255, description="备注")


class SkuCostUpdateSchema(BaseModel):
    """修改商品成本：两个商家编码都不承载——编码新增后不可修改，改编码等于删除重建"""

    sku_name: str | None = Field(default=None, max_length=128, description="规格名称")
    product_name: str | None = Field(default=None, min_length=1, max_length=255, description="商品名称")
    # 与新增同一条闸（AN13）：编辑也能把成本价改成 3 位小数，只在新增时拦等于没拦。
    cost_price: Decimal | None = Field(
        default=None, ge=0,
        description="单位采购成本（最多 2 位小数）；留空=缺失，0=有意0成本(ADR-0023)；负数一律拒绝(§15.4)",
    )
    cost_source: str | None = Field(default=None, description="成本来源(manual/import)")
    remark: str | None = Field(default=None, max_length=255, description="备注")


class SkuCostOutSchema(BaseSchema, UserBySchema):
    """商品成本响应模型。不继承 CreateSchema：规格商家编码可以为 NULL，响应模型不能把它声明成必填。"""

    model_config = ConfigDict(from_attributes=True)

    main_sku_code: str | None = Field(default=None, description="主商家编码")
    sku_code: str | None = Field(default=None, description="规格商家编码；NULL=该商品不分规格(ADR-0023)")
    sku_name: str | None = Field(default=None, description="规格名称")
    product_name: str = Field(description="商品名称")
    cost_price: Decimal | None = Field(default=None, description="单位采购成本；NULL=缺失，0=有意0成本(ADR-0023)")
    cost_source: str = Field(description="成本来源(manual/import)")
    remark: str | None = Field(default=None, description="备注")


class SkuCostQueryParam(BaseQueryParam, UserByQueryParam):
    """商品成本查询参数"""

    main_sku_code: str | None = Field(None, description="主商家编码", json_schema_extra={"q": "like"})
    sku_code: str | None = Field(None, description="规格商家编码", json_schema_extra={"q": "like"})
    product_name: str | None = Field(None, description="商品名称", json_schema_extra={"q": "like"})
    cost_source: str | None = Field(None, description="成本来源", json_schema_extra={"q": "eq"})
    cost_missing: bool | None = Field(None, description="成本价是否缺失（cost_price IS NULL）")


# ── 刷单核对(ScamReconciliation，§5.1第7-9点/ADR-0028) ───────────────────


class ScamApprovalDetailSchema(BaseModel):
    """钉钉「销量权重」审批明细中的一笔订单记录（供逐笔核对使用）"""

    process_instance_id: str = Field(description="钉钉审批实例ID")
    platform_order_no: str = Field(description="审批明细中记录的平台订单号")
    amount: Decimal = Field(description="审批金额（分精确），正向为正数，负向为负数")


class ScamReconciliationResultSchema(BaseModel):
    """单笔订单的刷单核对结果（落 settlement 相关清单展示用）"""

    model_config = ConfigDict(from_attributes=True)

    order_id: int = Field(description="订单ID")
    system_order_no: str = Field(description="系统订单号")
    platform_order_no: str = Field(description="平台订单号")
    order_amount: Decimal = Field(description="订单金额(buyer_paid_amount)")
    positive_approval_amount: Decimal | None = Field(default=None, description="正向销量权重审批金额")
    negative_approval_amount: Decimal | None = Field(default=None, description="负向销量权重审批金额")
    match_status: str = Field(description="匹配状态(matched/missing_approval/duplicate_approval/amount_mismatch/missing_negative)")
    reason: str | None = Field(default=None, description="异常原因说明")
