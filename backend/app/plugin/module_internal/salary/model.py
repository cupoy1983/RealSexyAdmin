from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base_model import ModelMixin, UserMixin

if TYPE_CHECKING:
    from app.modules.system.user.model import UserModel


class SalaryProfileModel(ModelMixin, UserMixin):
    """员工薪资档案：每人一份月度薪酬基准（ADR-0011）。"""

    __tablename__: str = "iac_salary_profile"

    oa_user_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False, unique=True, index=True, comment="OA用户ID")
    base_salary: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False, comment="基础工资(月)")
    full_attendance_bonus: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False, comment="全勤奖")
    allowance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False, comment="固定补贴(月)")
    performance_base: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False, comment="绩效基准(月)")
    status: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="状态(0:启用 1:停用)")
    user: Mapped["UserModel | None"] = relationship("UserModel", foreign_keys=[oa_user_id], lazy="selectin")


class PerformanceEntryModel(ModelMixin, UserMixin):
    """员工月度绩效扣款。工资条生成前可改，生成后固定。"""

    __tablename__: str = "iac_salary_performance_entry"
    __table_args__: tuple = (UniqueConstraint("oa_user_id", "month", name="uk_iac_salary_performance_user_month"),)

    oa_user_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False, index=True, comment="OA用户ID")
    month: Mapped[str] = mapped_column(String(7), nullable=False, index=True, comment="月份(YYYY-MM)")
    deduction: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False, comment="绩效扣款")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="扣款说明")
    status: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="状态(0:待确认 1:已确认)")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="绩效确认时间")
    confirmed_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="绩效确认人OA用户ID")
    user: Mapped["UserModel | None"] = relationship("UserModel", foreign_keys=[oa_user_id], lazy="selectin")


class PayslipModel(ModelMixin, UserMixin):
    """月度工资条：保存生成时的全部计算依据和结果，不随后续档案变化。"""

    __tablename__: str = "iac_salary_payslip"
    __table_args__: tuple = (UniqueConstraint("oa_user_id", "month", name="uk_iac_salary_payslip_user_month"),)

    oa_user_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False, index=True, comment="OA用户ID")
    month: Mapped[str] = mapped_column(String(7), nullable=False, index=True, comment="月份(YYYY-MM)")
    user_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="姓名快照")
    dept_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="部门快照")
    group_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="考勤组ID快照")
    group_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="考勤组名称快照")
    required_days: Mapped[float] = mapped_column(Float, default=0, nullable=False, comment="应出勤天数")
    actual_days: Mapped[float] = mapped_column(Float, default=0, nullable=False, comment="实际出勤天数")
    missing_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="缺卡总次数")
    base_salary: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False, comment="基础工资基准快照")
    full_attendance_bonus: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False, comment="全勤奖基准快照")
    allowance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False, comment="固定补贴快照")
    performance_base: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False, comment="绩效基准快照")
    performance_deduction: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False, comment="绩效扣款快照")
    prorated_base: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False, comment="折算基础工资")
    full_attendance_award: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False, comment="实发全勤奖")
    performance_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False, comment="实际绩效")
    missing_deduction: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False, comment="缺卡扣款(10*n^2)")
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False, comment="应发合计")
    net_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False, comment="实发工资")
    generation_source: Mapped[str] = mapped_column(String(16), default="manual", nullable=False, comment="生成方式(auto/manual)")
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="生成时间")
    status: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="状态(0:待确认 1:已确定; 离职结算单落库即1)")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="确认时间")
    confirmed_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="确认人OA用户ID")
    user: Mapped["UserModel | None"] = relationship("UserModel", foreign_keys=[oa_user_id], lazy="selectin")


class SalaryNotificationLogModel(ModelMixin):
    """三类钉钉通知结果；N2/N3 成功去重，失败留下可重试记录。"""

    __tablename__: str = "iac_salary_notification_log"
    __table_args__: tuple = (UniqueConstraint("event_type", "month", "oa_user_id", name="uk_iac_salary_notification_event_user_month"),)

    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True, comment="通知类型(admin_reminder/review/payslip_paid)")
    month: Mapped[str] = mapped_column(String(7), nullable=False, index=True, comment="业务月份(YYYY-MM)")
    oa_user_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False, index=True, comment="接收人OA用户ID")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="通知正文")
    status: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="状态(0:失败 1:成功)")
    error: Mapped[str | None] = mapped_column(Text, nullable=True, comment="最近失败原因")
    last_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="最近尝试时间")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="最近成功时间")
