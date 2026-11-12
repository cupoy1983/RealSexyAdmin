from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.core.base_schema import BaseQueryParam, BaseSchema, UserByQueryParam, UserBySchema
from app.core.validator import DateStr

# ── 账单解析规则(BillParseRule，ADR-0029) ────────────────────────────────


class BillColumnMapping(BaseModel):
    """快递账单 Excel 列映射配置（校验 column_mapping JSON 列，ADR-0029）

    各字段的值是该快递公司账单 Excel 里对应的中文表头名称，**五项全部必填**。
    bill_date / bill_weight 是异常判定与摊分的输入；receiver_province 是「省份异常」唯一的
    判定来源，规则漏配这一列会让判定静默失效（全部按省份相符通过），所以宁可建档时就拦。
    行内单元格仍可能空着（快递导出偶尔给 `-`），那种行跳过省份比较即可，不影响建档。
    """

    waybill_no: str = Field(..., min_length=1, description="运单号对应的表头名称")
    bill_date: str = Field(..., min_length=1, description="账单日期对应的表头名称")
    bill_weight: str = Field(..., min_length=1, description="账单重量对应的表头名称")
    bill_other_fee: str = Field(..., min_length=1, description="账单其它费用对应的表头名称")
    receiver_province: str = Field(..., min_length=1, description="收件省对应的表头名称")


class BillParseRuleCreateSchema(BaseModel):
    """新增账单解析规则"""

    bill_type: str = Field(..., min_length=1, max_length=32, description="账单类型编码（解析器分发键）")
    carrier_name: str = Field(..., min_length=1, max_length=64, description="快递公司显示名称")
    column_mapping: BillColumnMapping = Field(..., description="Excel表头列映射")
    # 面单费必填（§25 / ADR-0029）：原先 default=0 让「漏配」和「这家不收面单费」在库里长成同一个 0.00，
    # 而 0 进算式等于这一项不存在，一声不响。0 仍是合法值，只是必须人自己填。
    waybill_fee: Decimal = Field(
        ...,
        ge=0,
        le=100,
        decimal_places=2,
        description="面单费（必填；0 表示这家快递不收面单费）",
    )
    status: int = Field(default=0, ge=0, le=1, description="状态(0:正常 1:停用)")


class BillParseRuleUpdateSchema(BaseModel):
    """修改账单解析规则"""

    carrier_name: str | None = Field(default=None, min_length=1, max_length=64, description="快递公司显示名称")
    column_mapping: BillColumnMapping | None = Field(default=None, description="Excel表头列映射")
    # 更新也必填（AL8）：可空字段配上 crud 的 exclude_none=True，「清空面单费再保存」会被当成「这一项不改」——
    # 提示保存成功、老值原封不动，界面做不出它看起来在做的事。这条路堵死，不分启用/停用。
    waybill_fee: Decimal = Field(
        ...,
        ge=0,
        le=100,
        decimal_places=2,
        description="面单费（必填；0 表示这家快递不收面单费）",
    )
    status: int | None = Field(default=None, ge=0, le=1, description="状态(0:正常 1:停用)")


class BillParseRuleOutSchema(BaseSchema, UserBySchema):
    """账单解析规则响应模型"""

    model_config = ConfigDict(from_attributes=True)

    bill_type: str = Field(description="账单类型编码")
    carrier_name: str = Field(description="快递公司显示名称")
    column_mapping: dict = Field(description="Excel表头列映射")
    waybill_fee: Decimal = Field(description="面单费")
    status: int = Field(description="状态(0:正常 1:停用)")


class BillParseRuleQueryParam(BaseQueryParam, UserByQueryParam):
    """账单解析规则查询参数"""

    bill_type: str | None = Field(None, description="账单类型编码", json_schema_extra={"q": "eq"})
    carrier_name: str | None = Field(None, description="快递公司显示名称", json_schema_extra={"q": "like"})
    status: int | None = Field(None, description="状态", json_schema_extra={"q": "eq"})


# ── 快递账单原始行(CourierBill) ──────────────────────────────────────────


class CourierBillOutSchema(BaseSchema, UserBySchema):
    """账单行响应模型（「账单详情」tab 一票一行；导入判定产生，不对外暴露创建接口）"""

    model_config = ConfigDict(from_attributes=True)

    import_batch_id: str | None = Field(default=None, description="所属导入批次号")
    bill_month: str = Field(description="出账月份(YYYY-MM)")
    bill_date: DateStr | None = Field(default=None, description="账单日期")
    bill_type: str = Field(description="账单类型编码")
    waybill_no: str = Field(description="运单号")
    bill_weight: Decimal | None = Field(default=None, description="账单重量(kg)")
    receiver_province: str | None = Field(default=None, description="收件省")
    waybill_fee: Decimal = Field(description="面单费快照")
    bill_other_fee: Decimal = Field(description="账单其它费用")
    actual_freight: Decimal = Field(description="该行计入运费成本的金额（虚假/重复行为0）")
    store_id: int | None = Field(default=None, description="店铺ID")
    store_name: str | None = Field(default=None, description="店铺名称")
    status: str = Field(description="行状态(matched/fake/duplicate/weight_province/restored)")
    anomaly_remark: str | None = Field(default=None, description="异常原因")
    # 以下两项由服务层按运单号反查 ERP 发货子订单后填入，模型上不存在该属性
    system_order_nos: str = Field(default="", description="对应的系统订单号（多张用「、」拼接）")
    detail_count: int = Field(default=0, description="对应的 ERP 发货子订单数")


class CourierBillQueryParam(BaseQueryParam, UserByQueryParam):
    """账单行查询参数"""

    import_batch_id: str | None = Field(None, description="所属导入批次号", json_schema_extra={"q": "eq"})
    bill_month: str | None = Field(None, description="出账月份(YYYY-MM)", json_schema_extra={"q": "eq"})
    bill_type: str | None = Field(None, description="账单类型编码", json_schema_extra={"q": "eq"})
    waybill_no: str | None = Field(None, description="运单号", json_schema_extra={"q": "like"})
    store_id: int | None = Field(None, description="店铺ID", json_schema_extra={"q": "eq"})
    status: str | None = Field(None, description="行状态", json_schema_extra={"q": "eq"})


# ── 账单导入 / 批次确认出参 ──────────────────────────────────────────────


class BillImportResultSchema(BaseModel):
    """账单导入结果（导入即判定，本批各状态行数一览）"""

    batch_no: str = Field(description="所属批次号")
    row_count: int = Field(description="导入行数")
    matched: int = Field(default=0, description="已匹配(原件行)")
    weight_province: int = Field(default=0, description="重量省份异常")
    duplicate: int = Field(default=0, description="重复异常（待处理）")
    fake: int = Field(default=0, description="虚假异常（待处理）")
    message: str = Field(description="结果描述")


class CourierBillRestoreSchema(BaseModel):
    """虚假/重复行人工改对后置为「已恢复」；虚假行改对前必须先认领订单（§22.3）

    「已恢复」是终点（§23，AJ 轮）：一条账单行只有一次认领机会，不能再改、不能再退回。
    """

    ids: list[int] = Field(..., min_length=1, description="账单行ID列表；带系统订单号时只允许一行")
    remark: str | None = Field(default=None, max_length=255, description="人工改对的理由/说明，追加到异常原因")
    system_order_no: str | None = Field(
        default=None, max_length=64,
        description="认领订单：把本票运单号订正进哪个系统订单号（虚假行改对必填；重复行不许填）",
    )
    detail_ids: list[int] | None = Field(
        default=None,
        description="认领订单：勾选的子订单行ID（可多选=一票分几包发），须属于该主订单且ERP发货、非分销",
    )
