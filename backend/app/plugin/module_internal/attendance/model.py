from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base_model import ModelMixin, UserMixin

if TYPE_CHECKING:
    from app.modules.system.user.model import UserModel


class MonthlyReportModel(ModelMixin, UserMixin):
    """钉钉考勤月汇总表

    一个 OA 用户的一个月份只保留一条记录；重复同步覆盖旧结果。
    """

    __tablename__: str = "iac_attendance_monthly_report"
    __table_args__: tuple = (
        UniqueConstraint("oa_user_id", "month", name="uk_iac_attendance_monthly_report_user_month"),
    )

    oa_user_id: Mapped[int] = mapped_column(
        ForeignKey("sys_user.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
        index=True,
        comment="OA用户ID",
    )
    month: Mapped[str] = mapped_column(String(7), nullable=False, index=True, comment="统计月份(YYYY-MM)")

    # 当月快照：员工后来调部门/改名，历史报表仍保持当月样子
    name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="姓名快照")
    dept_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="部门快照")
    job_number: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="工号快照")
    group_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="考勤组ID快照(钉钉)")
    group_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="考勤组快照")

    # 钉钉统计列：NULL 表示钉钉没返回该列，0 表示钉钉算出来就是 0
    required_days: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="应出勤天数")
    # 出勤天数是浮点：新口径 = 应出勤 − 旷工 − 请假天数（ADR-0010），
    # 半天假会带出 0.5，所以不能用 Integer
    actual_days: Mapped[float | None] = mapped_column(Float, nullable=True, comment="出勤天数")
    rest_days: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="休息天数")
    late_count: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="迟到次数")
    serious_late_count: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="严重迟到次数")
    absent_late_days: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="旷工迟到天数")
    early_leave_count: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="早退次数")
    missing_checkin_count: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="上班缺卡次数")
    missing_checkout_count: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="下班缺卡次数")
    absent_days: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="旷工天数")

    leave_personal: Mapped[float | None] = mapped_column(Float, nullable=True, comment="事假(天)")
    leave_sick: Mapped[float | None] = mapped_column(Float, nullable=True, comment="病假(天)")
    leave_annual: Mapped[float | None] = mapped_column(Float, nullable=True, comment="年假(天)")

    # 钉钉原始月值快照：同步时写入一次，是公司休息日重算的基准（ADR-0009）。
    # 上面那些统计列可能被休息日重算改过，这里永远保留钉钉算出来的原值，
    # 增删休息日都能以它为基准幂等重算、删除也能精确还原。
    dingtalk_raw: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="钉钉原始月值快照(JSON)")

    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="最近同步时间")
    status: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="状态(0:启用 1:停用)")

    user: Mapped["UserModel | None"] = relationship("UserModel", foreign_keys=[oa_user_id], lazy="selectin")


class DailyReportModel(ModelMixin, UserMixin):
    """钉钉考勤日明细表

    一行对应钉钉导出 Excel 里的「一个日期格」，是「考勤结果」那 30 列的落库形态。
    展示与对账以 raw_text（钉钉原文）为准，不在 OA 侧重新判定考勤状态。
    """

    __tablename__: str = "iac_attendance_daily_report"
    __table_args__: tuple = (
        UniqueConstraint("oa_user_id", "work_date", name="uk_iac_attendance_daily_report_user_date"),
    )

    oa_user_id: Mapped[int] = mapped_column(
        ForeignKey("sys_user.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
        index=True,
        comment="OA用户ID",
    )
    work_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True, comment="考勤日期")
    month: Mapped[str] = mapped_column(String(7), nullable=False, index=True, comment="所属月份(YYYY-MM)，冗余便于按月查")
    day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="日号，冗余便于按列展示排序")

    group_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="考勤组ID快照")
    group_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="考勤组名称快照")

    raw_text: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="钉钉考勤结果原文")
    approve_text: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="关联的审批单原文")
    # 展示值：默认 = raw_text，公司休息日 → 「公司休息」（ADR-0009）。
    # raw_text 是钉钉原文、永不动；effective_text 是叠加公司休息日规则后的展示值，可被重算反复改写。
    effective_text: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="考勤结果展示值(叠加公司休息日)")

    # ── 预留列（照参考代码保留，当前不写值）────────────────────────
    late_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="迟到分钟(预留)")
    early_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="早退分钟(预留)")
    check_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="实际上班打卡(预留)")
    check_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="实际下班打卡(预留)")
    is_workday: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="是否应出勤日(预留)")

    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="最近同步时间")
    status: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="状态(0:启用 1:停用)")

    user: Mapped["UserModel | None"] = relationship("UserModel", foreign_keys=[oa_user_id], lazy="selectin")


class RestDayModel(ModelMixin, UserMixin):
    """公司休息日

    公司在考勤组原有规则之外、按「日期 + 考勤组」临时安排的不必出勤的日子（ADR-0009）。
    组里全员一起休；钉钉不知道它，由本系统记录并以钉钉月报为基准叠加它重算考勤。

    纯配置数据，删除用物理删（删了重加即可），所以 (group_id, rest_date) 唯一约束不会
    和软删除残留冲突。
    """

    __tablename__: str = "iac_attendance_rest_day"
    __table_args__: tuple = (
        UniqueConstraint("group_id", "rest_date", name="uk_iac_attendance_rest_day_group_date"),
    )

    group_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="考勤组ID(钉钉)")
    group_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="考勤组名称快照")
    rest_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True, comment="休息日期")
    month: Mapped[str] = mapped_column(String(7), nullable=False, index=True, comment="所属月份(YYYY-MM)，冗余便于按月查")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="备注(如 中秋补休)")
    status: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="状态(0:启用 1:停用)")
