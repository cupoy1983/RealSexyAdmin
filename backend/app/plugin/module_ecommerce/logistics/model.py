from datetime import date
from decimal import Decimal

from sqlalchemy import JSON, Date, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.orm.base import LoaderCallableStatus, instance_state

from app.core.base_model import ModelMixin, UserMixin

# 账单行状态（五值，ADR-0034）
STATUS_MATCHED = "matched"                        # 已匹配：原件行，参与回写
STATUS_FAKE = "fake"                              # 虚假异常：运单号在 ERP 发货子订单里查不到
STATUS_DUPLICATE = "duplicate"                    # 重复异常：同运单号已有原件行
STATUS_WEIGHT_PROVINCE = "weight_province"        # 重量省份异常：只标记，钱照算
STATUS_RESTORED = "restored"                      # 已恢复：虚假/重复经人工改对后置此

ALL_STATUSES: tuple[str, ...] = (
    STATUS_MATCHED, STATUS_FAKE, STATUS_DUPLICATE, STATUS_WEIGHT_PROVINCE, STATUS_RESTORED,
)

# 原件行 = 参与摊分回写、且作为后续导入判重基线的行。
# 重量省份异常只标记不动钱，所以它仍是原件行；虚假/重复不是。
ORIGINAL_STATUSES: tuple[str, ...] = (
    STATUS_MATCHED, STATUS_WEIGHT_PROVINCE, STATUS_RESTORED,
)

# 需要人工处理、否则拦住批次确认的状态
BLOCKING_STATUSES: tuple[str, ...] = (STATUS_FAKE, STATUS_DUPLICATE)


class BillParseRuleModel(ModelMixin, UserMixin):
    """快递账单解析规则 - 按 bill_type 分发列映射配置（独立表，ADR-0029）

    字典的 dict_value 字段仅 String(255)，装不下结构化列映射，且字典管理页无 JSON
    格式校验，录入错误只能等到解析账单时才暴雷——这块配置直接决定 actual_freight，
    换独立表的字段级校验成本换运费计算的可靠性是划算的。
    """

    __tablename__: str = "ecommerce_bill_parse_rule"
    __table_args__: dict[str, str] = {"comment": "快递账单解析规则（按bill_type分发，ADR-0029）"}

    bill_type: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True, comment="账单类型编码（解析器分发键）")
    carrier_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="快递公司显示名称")
    column_mapping: Mapped[dict] = mapped_column(JSON, nullable=False, comment="Excel表头列名映射（校验见 BillColumnMapping）")
    waybill_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, nullable=False, comment="面单费（固定值，按账单类型配置）")
    status: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="状态(0:正常 1:停用)")


class CourierBillModel(ModelMixin, UserMixin):
    """快递账单原始行 - 一票一行，判定结果与摊分金额直接落在本行（ADR-0034）

    原先另建 ecommerce_freight_match 存匹配结果，摊分要跨两表、判重要读子订单、
    确认时还得先删后插；现在账单行自己就是结论，运费成本回写到子订单后即唯一事实。
    """

    __tablename__: str = "ecommerce_courier_bill"
    __table_args__: dict[str, str] = {"comment": "快递账单原始行（一票一行，含判定与摊分结果）"}

    import_batch_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
        comment="所属导入批次号(bill-{账单类型}-{出账月})；必填，挂不到批次的行按脏数据处理，见 ADR-0033",
    )
    bill_month: Mapped[str] = mapped_column(String(7), nullable=False, index=True, comment="出账月份(YYYY-MM)")
    bill_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True, comment="账单日期（行级，必须落在 bill_month 内）")
    bill_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True, comment="账单类型编码（对应 BillParseRuleModel.bill_type）")
    waybill_no: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="运单号（全局匹配 ERP 发货子订单的键）")
    bill_weight: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 3), default=None, nullable=True, comment="账单重量(kg)；为空则不判重量异常",
    )
    receiver_province: Mapped[str | None] = mapped_column(
        String(32), default=None, nullable=True, comment="收件省（已归一化）；为空则不判省份异常",
    )
    waybill_fee: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=0, nullable=False, comment="面单费快照（导入时取解析规则当时的固定值，后续改规则不回溯）",
    )
    bill_other_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, nullable=False, comment="账单其它费用")
    actual_freight: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=0, nullable=False, comment="该行计入运费成本的金额：原件行=面单费+其它费用，虚假/重复行=0",
    )
    store_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ecommerce_store.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True, index=True, comment="店铺ID（摊分到的最早子订单所属店铺；跨店铺时在 anomaly_remark 追加「跨店铺」）",
    )
    status: Mapped[str] = mapped_column(
        String(24), default=STATUS_FAKE, nullable=False, index=True,
        comment="行状态(matched:已匹配 fake:虚假异常 duplicate:重复异常 weight_province:重量省份异常 restored:已恢复)；默认值仅为兜底，导入判定必然覆盖",
    )
    anomaly_remark: Mapped[str | None] = mapped_column(
        Text, default=None, nullable=True, comment="异常原因文本（只往后追加，不覆盖已有说明）",
    )

    store: Mapped["StoreModel | None"] = relationship("StoreModel", foreign_keys="CourierBillModel.store_id", uselist=False)  # noqa: F821

    @property
    def store_name(self) -> str | None:
        """店铺名称（供序列化用；仅在 store 关系已预加载时取值，避免异步懒加载）。"""
        loaded = instance_state(self).dict.get("store", LoaderCallableStatus.NEVER_SET)
        if loaded in (LoaderCallableStatus.NEVER_SET, LoaderCallableStatus.PASSIVE_NO_RESULT):
            return None
        return loaded.store_name if loaded is not None else None
