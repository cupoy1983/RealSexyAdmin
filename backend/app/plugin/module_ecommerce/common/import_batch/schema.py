"""通用输出/查询模型 - 导入批次（ADR-0033）。

批次只能由导入流程创建，不对外暴露 create/update 接口；确认动作分别由
logistics（账单批次，含运费回写）与 order（订单批次）自己的端点承担。
"""

from pydantic import BaseModel, ConfigDict, Field

from app.core.base_schema import BaseQueryParam, BaseSchema, UserByQueryParam, UserBySchema
from app.core.validator import DateTimeStr


class ImportBatchOutSchema(BaseSchema, UserBySchema):
    """导入批次响应模型"""

    model_config = ConfigDict(from_attributes=True)

    batch_no: str = Field(description="批次号(order-2026-09 / bill-yto-2026-09)")
    file_category: str = Field(description="文件类别(order/bill)")
    sub_category: str | None = Field(default=None, description="子类别（账单快递公司编码，订单为空）")
    batch_month: str = Field(description="批次月份(YYYY-MM)：订单=发货月份，账单=出账月份")
    status: str = Field(description="批次状态(unconfirmed/confirmed)")
    confirmed_time: DateTimeStr | None = Field(default=None, description="确认时间")
    row_count: int = Field(description="导入行数")
    order_count: int = Field(description="业务对象数（主订单数/运单票数）")
    remark: str | None = Field(default=None, description="批次提示")


class ImportBatchQueryParam(BaseQueryParam, UserByQueryParam):
    """导入批次查询参数"""

    batch_no: str | None = Field(None, description="批次号", json_schema_extra={"q": "like"})
    file_category: str | None = Field(None, description="文件类别(order/bill)", json_schema_extra={"q": "eq"})
    sub_category: str | None = Field(None, description="子类别", json_schema_extra={"q": "eq"})
    batch_month: str | None = Field(None, description="批次月份(YYYY-MM)", json_schema_extra={"q": "eq"})
    status: str | None = Field(None, description="批次状态", json_schema_extra={"q": "eq"})


class BillBatchCountSchema(BaseModel):
    """账单批次详情顶部的计数行（正常/重复/虚假/重量省份 + 发货月分布）"""

    total: int = Field(description="账单行数（不含已删除）")
    matched: int = Field(description="已匹配")
    duplicate: int = Field(description="重复异常（待处理）")
    fake: int = Field(description="虚假异常（待处理）")
    weight_province: int = Field(description="重量省份异常")
    restored: int = Field(description="已恢复（人工改对，钱计入成本）")
    ship_month_distribution: dict[str, int] = Field(
        default_factory=dict,
        description="该批各票命中子订单的发货月 → 票数（按运单号去重，挂不上订单的票不计；现算不落库）",
    )


class BillGateGapSchema(BaseModel):
    """订单批次确认门禁的缺口清单（ADR-0033 + Z5；④ 是脏数据报警，不参与门禁判定）"""

    bill_month: str = Field(description="被检查的出账月份(YYYY-MM)")
    unconfirmed_batches: list[str] = Field(default_factory=list, description="① 已存在但还没确认的账单批次号")
    missing_types: list[str] = Field(default_factory=list, description="② 本月完全没有批次的启用中账单类型编码")
    no_active_rules: bool = Field(
        default=False,
        description="③ 启用中的解析规则一条都没有（Z5）：这不是「本月不需要账单」，门禁一律不过",
    )
    orphan_bill_rows: int = Field(
        default=0,
        description="④ 该月带着批次号、但批次表里已无此批次的账单行数。这是脏数据报警不是缺口："
        "只有手工删过批次行才会造成，不参与门禁判定（这种行本来就确认不了），但必须让人看见",
    )

    @property
    def passed(self) -> bool:
        return not (self.unconfirmed_batches or self.missing_types or self.no_active_rules)


class BillBatchDeclareSchema(BaseModel):
    """声明「本月无账单」：为列出的类型生成零行未确认批次"""

    bill_month: str = Field(min_length=7, max_length=7, description="出账月份(YYYY-MM)")
    bill_types: list[str] = Field(min_length=1, description="要声明的账单类型编码（快递公司的 bill_type）")


class BatchConfirmResultSchema(BaseModel):
    """批次确认结果"""

    batch_no: str = Field(description="批次号")
    message: str = Field(description="结果描述")
    warning: str | None = Field(default=None, description="软提示（如成本缺失），不阻断确认")
