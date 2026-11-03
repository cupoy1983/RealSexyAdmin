from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.base_schema import BaseQueryParam, BaseSchema, UserByQueryParam, UserBySchema

_MONTH_PATTERN_LENGTH = 7


class MonthlyReportCreateSchema(BaseModel):
    """钉钉考勤月汇总创建/更新模型"""

    oa_user_id: int = Field(..., ge=1, description="OA用户ID")
    month: str = Field(..., min_length=7, max_length=7, description="统计月份(YYYY-MM)")
    name: str | None = Field(default=None, max_length=64, description="姓名快照")
    dept_name: str | None = Field(default=None, max_length=128, description="部门快照")
    job_number: str | None = Field(default=None, max_length=64, description="工号快照")
    group_id: str | None = Field(default=None, max_length=64, description="考勤组ID快照")
    group_name: str | None = Field(default=None, max_length=64, description="考勤组快照")

    required_days: int | None = Field(default=None, description="应出勤天数")
    actual_days: float | None = Field(default=None, description="出勤天数")
    rest_days: int | None = Field(default=None, description="休息天数")
    late_count: int | None = Field(default=None, description="迟到次数")
    serious_late_count: int | None = Field(default=None, description="严重迟到次数")
    absent_late_days: int | None = Field(default=None, description="旷工迟到天数")
    early_leave_count: int | None = Field(default=None, description="早退次数")
    missing_checkin_count: int | None = Field(default=None, description="上班缺卡次数")
    missing_checkout_count: int | None = Field(default=None, description="下班缺卡次数")
    absent_days: int | None = Field(default=None, description="旷工天数")

    leave_personal: float | None = Field(default=None, description="事假(天)")
    leave_sick: float | None = Field(default=None, description="病假(天)")
    leave_annual: float | None = Field(default=None, description="年假(天)")

    synced_at: datetime | None = Field(default=None, description="最近同步时间")
    status: int = Field(default=0, ge=0, le=1, description="状态(0:启用 1:停用)")

    @field_validator("month")
    @classmethod
    def _check_month(cls, v: str) -> str:
        v = (v or "").strip()
        if len(v) != _MONTH_PATTERN_LENGTH or v[4] != "-":
            raise ValueError("月份格式必须为 YYYY-MM")
        year, mon = v.split("-")
        if not (year.isdigit() and mon.isdigit()) or not (1 <= int(mon) <= 12):
            raise ValueError("月份格式必须为 YYYY-MM")
        return v


class MonthlyReportUpdateSchema(MonthlyReportCreateSchema):
    """钉钉考勤月汇总更新模型"""


class MonthlyReportOutSchema(MonthlyReportCreateSchema, BaseSchema, UserBySchema):
    """钉钉考勤月汇总响应模型"""

    model_config = ConfigDict(from_attributes=True)

    user_name: str | None = Field(default=None, description="当前OA用户姓名")
    current_dept_name: str | None = Field(default=None, description="当前OA部门名称")


class MonthlyReportQueryParam(BaseQueryParam, UserByQueryParam):
    """钉钉考勤月汇总查询参数"""

    month: str | None = Field(None, description="统计月份(YYYY-MM)", json_schema_extra={"q": "eq"})
    oa_user_id: int | None = Field(None, description="OA用户ID", json_schema_extra={"q": "eq"})
    name: str | None = Field(None, description="姓名", json_schema_extra={"q": "like"})
    dept_id: int | None = Field(None, description="部门ID", json_schema_extra={"q": "eq"})


class DailyReportCreateSchema(BaseModel):
    """钉钉考勤日明细创建/更新模型"""

    oa_user_id: int = Field(..., ge=1, description="OA用户ID")
    work_date: date = Field(..., description="考勤日期")
    month: str = Field(..., min_length=7, max_length=7, description="所属月份(YYYY-MM)")
    day_of_month: int | None = Field(default=None, description="日号")
    group_id: str | None = Field(default=None, max_length=64, description="考勤组ID快照")
    group_name: str | None = Field(default=None, max_length=64, description="考勤组名称快照")
    raw_text: str | None = Field(default=None, max_length=512, description="钉钉考勤结果原文")
    approve_text: str | None = Field(default=None, max_length=512, description="关联的审批单原文")
    synced_at: datetime | None = Field(default=None, description="最近同步时间")
    status: int = Field(default=0, ge=0, le=1, description="状态(0:启用 1:停用)")


class DailyReportUpdateSchema(DailyReportCreateSchema):
    """钉钉考勤日明细更新模型"""


class DailyReportOutSchema(BaseSchema, UserBySchema):
    """钉钉考勤日明细响应模型（一行 = 钉钉导出报表里的一个日期格）"""

    model_config = ConfigDict(from_attributes=True)

    oa_user_id: int = Field(..., description="OA用户ID")
    work_date: date | None = Field(default=None, description="考勤日期")
    month: str = Field(default="", description="所属月份(YYYY-MM)")
    day_of_month: int | None = Field(default=None, description="日号")
    group_id: str | None = Field(default=None, description="考勤组ID快照")
    group_name: str | None = Field(default=None, description="考勤组名称快照")
    raw_text: str | None = Field(default=None, description="钉钉考勤结果原文")
    approve_text: str | None = Field(default=None, description="关联的审批单原文")
    effective_text: str | None = Field(default=None, description="考勤结果展示值(叠加公司休息日，ADR-0009)")
    synced_at: datetime | None = Field(default=None, description="最近同步时间")


class DailyReportQueryParam(BaseQueryParam, UserByQueryParam):
    """钉钉考勤日明细查询参数"""

    month: str | None = Field(None, description="所属月份(YYYY-MM)", json_schema_extra={"q": "eq"})
    oa_user_id: int | None = Field(None, description="OA用户ID", json_schema_extra={"q": "eq"})


class MonthlyReportSyncResultSchema(BaseModel):
    """月报同步结果"""

    month: str = Field(default="", description="同步月份")
    start_date: str = Field(default="", description="开始日期")
    end_date: str = Field(default="", description="结束日期")
    total: int = Field(default=0, description="待同步人数")
    success: int = Field(default=0, description="同步成功人数")
    failed: int = Field(default=0, description="同步失败人数")
    skipped: int = Field(default=0, description="没有考勤组而跳过的人数")
    messages: list[str] = Field(default_factory=list, description="失败明细")


class MonthlyReportSyncStateSchema(BaseModel):
    """上个月的考勤同步状态 —— 页面据此决定要不要显示「手动拉取」按钮

    can_sync 为 True 才显示按钮：既要有「有考勤组」的人可拉，
    又要求上个月还没被拉过（自动与手动互斥，谁先跑谁生效）。
    """

    month: str = Field(default="", description="上个月(YYYY-MM)")
    synced: bool = Field(default=False, description="上个月是否已同步过")
    can_sync: bool = Field(default=False, description="现在是否可以手动拉取")


# ── 公司休息日（ADR-0009）─────────────────────────────────────


class RestDayCreateSchema(BaseModel):
    """公司休息日批量创建模型：一个考勤组 + 多个日期"""

    group_id: str = Field(..., min_length=1, max_length=64, description="考勤组ID(钉钉)")
    dates: list[date] = Field(..., min_length=1, description="休息日期列表(YYYY-MM-DD)")
    remark: str | None = Field(default=None, max_length=255, description="备注")


class RestDayCreateResultSchema(BaseModel):
    """休息日创建结果"""

    created: int = Field(default=0, description="本次新增条数")
    skipped_dates: list[str] = Field(default_factory=list, description="已存在而跳过的日期")
    months: list[str] = Field(default_factory=list, description="涉及并已重算的月份")


class RestDayUpdateSchema(BaseModel):
    """休息日更新模型（预留：休息日以创建/删除为主，暂不提供编辑）"""

    remark: str | None = Field(default=None, max_length=255, description="备注")


class RestDayOutSchema(BaseSchema, UserBySchema):
    """公司休息日响应模型"""

    model_config = ConfigDict(from_attributes=True)

    group_id: str = Field(..., description="考勤组ID")
    group_name: str | None = Field(default=None, description="考勤组名称快照")
    rest_date: date | None = Field(default=None, description="休息日期")
    month: str = Field(default="", description="所属月份(YYYY-MM)")
    remark: str | None = Field(default=None, description="备注")


class RestDayQueryParam(BaseQueryParam):
    """公司休息日查询参数"""

    month: str | None = Field(None, description="所属月份(YYYY-MM)", json_schema_extra={"q": "eq"})
    group_id: str | None = Field(None, description="考勤组ID", json_schema_extra={"q": "eq"})


class AttendanceGroupOptionSchema(BaseModel):
    """考勤组下拉选项（取自绑定表里有考勤组的记录）"""

    group_id: str = Field(..., description="考勤组ID")
    group_name: str | None = Field(default=None, description="考勤组名称")
