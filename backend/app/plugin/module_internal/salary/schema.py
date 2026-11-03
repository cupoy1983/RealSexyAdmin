from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.base_schema import BaseSchema


def _validate_month(value: str) -> str:
    value = (value or "").strip()
    if len(value) != 7 or value[4] != "-":
        raise ValueError("月份格式必须为 YYYY-MM")
    year, month = value.split("-")
    if not (year.isdigit() and month.isdigit()) or not (1 <= int(month) <= 12):
        raise ValueError("月份格式必须为 YYYY-MM")
    return value


class SalaryProfileUpdateSchema(BaseModel):
    base_salary: float = Field(default=0, ge=0, description="基础工资(月)")
    full_attendance_bonus: float = Field(default=0, ge=0, description="全勤奖")
    allowance: float = Field(default=0, ge=0, description="固定补贴(月)")
    performance_base: float = Field(default=0, ge=0, description="绩效基准(月)")


class SalaryProfileOutSchema(SalaryProfileUpdateSchema):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = Field(default=None, description="薪资档案ID，未设置时为空")
    oa_user_id: int = Field(..., description="OA用户ID")
    user_name: str | None = Field(default=None, description="员工姓名")
    group_name: str | None = Field(default=None, description="考勤组")
    has_profile: bool = Field(default=False, description="是否已设置薪资档案")


class PerformanceEntrySaveSchema(BaseModel):
    oa_user_id: int = Field(..., ge=1, description="OA用户ID")
    month: str = Field(..., min_length=7, max_length=7, description="月份(YYYY-MM)")
    deduction: float = Field(default=0, ge=0, description="绩效扣款")
    remark: str | None = Field(default=None, max_length=255, description="扣款说明")

    _check_month = field_validator("month")(_validate_month)


class PerformanceEntryOutSchema(BaseModel):
    id: int | None = Field(default=None, description="绩效记录ID")
    oa_user_id: int = Field(..., description="OA用户ID")
    user_name: str | None = Field(default=None, description="员工姓名")
    group_name: str | None = Field(default=None, description="考勤组")
    month: str = Field(..., description="月份")
    performance_base: float = Field(default=0, description="绩效基准")
    deduction: float = Field(default=0, description="绩效扣款")
    performance_amount: float = Field(default=0, description="实际绩效(生成前预览)")
    remark: str | None = Field(default=None, description="扣款说明")
    locked: bool = Field(default=False, description="工资条已生成，禁止修改")
    attendance_ready: bool = Field(default=False, description="当月考勤已同步，可以确认绩效")
    requires_performance: bool = Field(default=True, description="是否有绩效奖金，需要管理员设置与确认")
    confirmed: bool = Field(default=False, description="绩效是否已确认")
    confirmed_at: datetime | None = Field(default=None, description="绩效确认时间")
    confirmed_id: int | None = Field(default=None, description="绩效确认人ID")


class PerformanceConfirmSchema(BaseModel):
    oa_user_id: int = Field(..., ge=1, description="OA用户ID")
    month: str = Field(..., min_length=7, max_length=7, description="月份(YYYY-MM)")
    confirmed: bool = Field(..., description="True确认，False撤销确认")

    _check_month = field_validator("month")(_validate_month)


class PerformanceConfirmResultSchema(BaseModel):
    month: str = Field(..., description="月份")
    confirmed_count: int = Field(default=0, description="已确认人数")
    total_count: int = Field(default=0, description="应确认总人数")
    all_confirmed: bool = Field(default=False, description="是否全部确认")
    payslips_generated: int = Field(default=0, description="本次自动生成工资条人数")
    message: str = Field(default="", description="处理结果说明")


class PayslipOutSchema(BaseSchema):
    model_config = ConfigDict(from_attributes=True)

    oa_user_id: int = Field(..., description="OA用户ID")
    month: str = Field(..., description="月份")
    user_name: str | None = Field(default=None, description="姓名")
    dept_name: str | None = Field(default=None, description="部门")
    group_name: str | None = Field(default=None, description="考勤组")
    required_days: float = Field(default=0, description="应出勤天数")
    actual_days: float = Field(default=0, description="实际出勤天数")
    missing_count: int = Field(default=0, description="缺卡次数")
    base_salary: float = Field(default=0, description="基础工资基准")
    full_attendance_bonus: float = Field(default=0, description="全勤奖基准")
    allowance: float = Field(default=0, description="固定补贴")
    performance_base: float = Field(default=0, description="绩效基准")
    performance_deduction: float = Field(default=0, description="绩效扣款")
    prorated_base: float = Field(default=0, description="折算基础工资")
    full_attendance_award: float = Field(default=0, description="实发全勤奖")
    performance_amount: float = Field(default=0, description="实际绩效")
    missing_deduction: float = Field(default=0, description="缺卡扣款")
    gross_amount: float = Field(default=0, description="应发合计")
    net_amount: float = Field(default=0, description="实发工资")
    generation_source: str = Field(default="manual", description="生成方式")
    generated_at: datetime | None = Field(default=None, description="生成时间")
    status: int = Field(default=0, description="0:未确认 1:已确认")
    confirmed_at: datetime | None = Field(default=None, description="确认时间")
    confirmed_id: int | None = Field(default=None, description="确认人ID")


class PayslipGenerationResultSchema(BaseModel):
    month: str = Field(..., description="生成月份")
    generated: int = Field(default=0, description="本次生成人数")
    already_generated: bool = Field(default=False, description="是否已经生成过")
    messages: list[str] = Field(default_factory=list, description="补充说明")
