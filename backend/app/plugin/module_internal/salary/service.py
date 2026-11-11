from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio.client import Redis

from app.core.ap_scheduler import SchedulerUtil
from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.modules.system.user.model import UserModel
from app.plugin.module_internal.account.model import ExternalAccountBindingModel
from app.plugin.module_internal.account.schema import SYSTEM_DINGTALK
from app.plugin.module_internal.attendance.model import DailyReportModel, MonthlyReportModel, RestDayModel
from app.plugin.module_internal.attendance.service import MonthlyReportService, _categorize_raw

from ..config import RestDayGroupPolicy, load_policy
from .model import PayslipModel, PerformanceEntryModel, SalaryProfileModel
from .notification import SalaryNotificationService
from .schema import (
    BusinessCalendarSchema,
    DismissalContextResultSchema,
    DismissalDeductionSchema,
    DismissalEmployeeContextSchema,
    DismissalSettlementResultSchema,
    PerformanceConfirmResultSchema,
    PerformanceConfirmSchema,
    PerformanceEntryOutSchema,
    PerformanceEntrySaveSchema,
    PayslipGenerationResultSchema,
    PayslipOutSchema,
    SalaryProfileOutSchema,
    SalaryProfileUpdateSchema,
)

MANAGE_PERMISSION = "module_internal:salary-performance:manage"
# ADR-0016：离职结算单与正式工资条共用 iac_salary_payslip，只靠 generation_source 区分。
DISMISSAL_SOURCE = "dismissal"
# 只是提示语，不参与任何金额计算（杜总 2026-11-09 定稿）。
DISMISSAL_TIP = "未满半个月无绩效，未满一个月扣除半月绩效！"
_MONEY_STEP = Decimal("0.01")


@dataclass
class _DismissalMonthPlan:
    month: str
    is_current_month: bool
    report: MonthlyReportModel | None
    deduction: Decimal
    will_generate: bool
    reason: str | None
    values: dict[str, Any] | None


@dataclass
class _DismissalPlan:
    user: UserModel
    profile: SalaryProfileModel | None
    previous_payslip_exists: bool
    deduction: Decimal
    months: list[_DismissalMonthPlan]
    messages: list[str] = field(default_factory=list)

# 「补工资」方案里，当天钉钉原文属于这些类别才算实际到岗、才发当天补贴。
# 请假 / 旷工 / 钉钉休息日 / 无数据 / 不认识一律不发；迟到 / 早退 / 缺卡算
# 「来了但有异常」，照发补贴，缺卡仍按现行 10*n² 扣款。
PAID_REST_CATEGORIES = frozenset({"normal", "field", "late", "early", "missing"})


def _money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)


class SalaryService:
    """薪资档案、月度绩效与工资条服务（ADR-0011）。"""

    def __init__(self, auth: AuthSchema, db: AsyncSession, redis: Redis | None = None) -> None:
        self.auth = auth
        self.db = db
        self.redis = redis or SchedulerUtil.redis_instance

    @property
    def can_manage(self) -> bool:
        return bool(self.auth.user.is_superuser or MANAGE_PERMISSION in (self.auth.permissions or []))

    def _require_manager(self) -> None:
        if not self.can_manage:
            raise CustomException(msg="只有管理员可以管理薪资绩效")

    def _require_superuser(self) -> None:
        if not self.auth.user.is_superuser:
            raise CustomException(msg="只有超级管理员可以执行此操作")

    @staticmethod
    def previous_month(today: date | None = None) -> str:
        today = today or date.today()
        return (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")

    async def _group_binding(self, oa_user_id: int) -> ExternalAccountBindingModel | None:
        return (
            await self.db.execute(
                select(ExternalAccountBindingModel).where(
                    ExternalAccountBindingModel.oa_user_id == oa_user_id,
                    ExternalAccountBindingModel.system_type == SYSTEM_DINGTALK,
                    ExternalAccountBindingModel.group_id.isnot(None),
                    ExternalAccountBindingModel.is_deleted == False,  # noqa: E712
                )
            )
        ).scalars().first()

    # ── 工资条查询：普通员工强制只看自己 ─────────────────────────
    def _payslip_conditions(self, *, month: str | None = None, name: str | None = None, oa_user_id: int | None = None) -> list[Any]:
        # ADR-0016 第二节：正式工资条的读路径默认排除离职结算单，结算单走 list_dismissal_payslips。
        conditions: list[Any] = [
            PayslipModel.is_deleted == False,  # noqa: E712
            PayslipModel.generation_source != DISMISSAL_SOURCE,
        ]
        if self.can_manage:
            if oa_user_id:
                conditions.append(PayslipModel.oa_user_id == oa_user_id)
        else:
            conditions.append(PayslipModel.oa_user_id == self.auth.user.id)
        if month:
            conditions.append(PayslipModel.month == month)
        if name:
            conditions.append(PayslipModel.user_name.like(f"%{name}%"))
        return conditions

    async def page_payslips(
        self,
        *,
        page_no: int = 1,
        page_size: int = 20,
        month: str | None = None,
        name: str | None = None,
        oa_user_id: int | None = None,
    ) -> PageResultSchema[PayslipOutSchema]:
        conditions = self._payslip_conditions(month=month, name=name, oa_user_id=oa_user_id)
        total = (await self.db.execute(select(func.count(PayslipModel.id)).where(*conditions))).scalar() or 0
        offset = (page_no - 1) * page_size
        rows = (
            await self.db.execute(
                select(PayslipModel)
                .where(*conditions)
                .order_by(PayslipModel.month.desc(), PayslipModel.oa_user_id.asc())
                .offset(offset)
                .limit(page_size)
            )
        ).scalars().all()
        return PageResultSchema(
            page_no=page_no,
            page_size=page_size,
            total=total,
            has_next=offset + page_size < total,
            items=[PayslipOutSchema.model_validate(row) for row in rows],
        )

    async def get_payslip(self, id: int) -> PayslipOutSchema:
        conditions = self._payslip_conditions()
        conditions.append(PayslipModel.id == id)
        row = (await self.db.execute(select(PayslipModel).where(*conditions))).scalars().first()
        if not row:
            raise CustomException(msg="工资条不存在或无权查看")
        return PayslipOutSchema.model_validate(row)

    # ── 薪资档案：管理员只读，超管可改 ───────────────────────────
    async def list_profiles(self, *, name: str | None = None) -> list[SalaryProfileOutSchema]:
        self._require_manager()
        conditions = [
            UserModel.is_deleted == False,  # noqa: E712
            UserModel.status == 0,
            ExternalAccountBindingModel.is_deleted == False,  # noqa: E712
            ExternalAccountBindingModel.system_type == SYSTEM_DINGTALK,
            ExternalAccountBindingModel.group_id.isnot(None),
        ]
        if name:
            conditions.append(UserModel.name.like(f"%{name}%"))
        rows = (
            await self.db.execute(
                select(UserModel, SalaryProfileModel, ExternalAccountBindingModel.group_name)
                .join(ExternalAccountBindingModel, ExternalAccountBindingModel.oa_user_id == UserModel.id)
                .outerjoin(
                    SalaryProfileModel,
                    and_(SalaryProfileModel.oa_user_id == UserModel.id, SalaryProfileModel.is_deleted == False),  # noqa: E712
                )
                .where(*conditions)
                .order_by(UserModel.id.asc())
            )
        ).all()
        result: list[SalaryProfileOutSchema] = []
        for user, profile, group_name in rows:
            result.append(
                SalaryProfileOutSchema(
                    id=profile.id if profile else None,
                    oa_user_id=user.id,
                    user_name=user.name,
                    group_name=group_name,
                    has_profile=profile is not None,
                    base_salary=float(profile.base_salary) if profile else 0,
                    full_attendance_bonus=float(profile.full_attendance_bonus) if profile else 0,
                    allowance=float(profile.allowance) if profile else 0,
                    performance_base=float(profile.performance_base) if profile else 0,
                )
            )
        return result

    async def save_profile(self, *, oa_user_id: int, data: SalaryProfileUpdateSchema) -> SalaryProfileOutSchema:
        self._require_superuser()
        binding = await self._group_binding(oa_user_id)
        if not binding:
            raise CustomException(msg="该员工没有考勤组，不参与薪酬计算")
        profile = (
            await self.db.execute(select(SalaryProfileModel).where(SalaryProfileModel.oa_user_id == oa_user_id))
        ).scalars().first()
        values = {key: _money(value) for key, value in data.model_dump().items()}
        if profile:
            for key, value in values.items():
                setattr(profile, key, value)
            profile.is_deleted = False
            profile.updated_id = self.auth.user.id
        else:
            profile = SalaryProfileModel(oa_user_id=oa_user_id, created_id=self.auth.user.id, **values)
            self.db.add(profile)
        await self.db.flush()
        return SalaryProfileOutSchema(
            id=profile.id,
            oa_user_id=oa_user_id,
            user_name=binding.user.name if binding.user else None,
            group_name=binding.group_name,
            has_profile=True,
            **data.model_dump(),
        )

    # ── 月度绩效：管理员可改，但工资条生成后锁定 ─────────────────
    async def month_generated(self, month: str) -> bool:
        # ADR-0016 第二节：结算单不能把整月判成「已生成」，否则绩效修改 / 自动生成全被锁死。
        count = (
            await self.db.execute(
                select(func.count(PayslipModel.id)).where(
                    PayslipModel.month == month,
                    PayslipModel.generation_source != DISMISSAL_SOURCE,
                    PayslipModel.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar() or 0
        return count > 0

    async def list_performance(self, *, month: str, name: str | None = None) -> list[PerformanceEntryOutSchema]:
        self._require_manager()
        locked = await self.month_generated(month)
        attendance_ready = await MonthlyReportService(self.auth, self.db, self.redis).month_synced(month)
        conditions = [
            UserModel.is_deleted == False,  # noqa: E712
            UserModel.status == 0,
            ExternalAccountBindingModel.is_deleted == False,  # noqa: E712
            ExternalAccountBindingModel.system_type == SYSTEM_DINGTALK,
            ExternalAccountBindingModel.group_id.isnot(None),
        ]
        if name:
            conditions.append(UserModel.name.like(f"%{name}%"))
        rows = (
            await self.db.execute(
                select(UserModel, SalaryProfileModel, PerformanceEntryModel, ExternalAccountBindingModel.group_name)
                .join(ExternalAccountBindingModel, ExternalAccountBindingModel.oa_user_id == UserModel.id)
                .outerjoin(
                    SalaryProfileModel,
                    and_(SalaryProfileModel.oa_user_id == UserModel.id, SalaryProfileModel.is_deleted == False),  # noqa: E712
                )
                .outerjoin(
                    PerformanceEntryModel,
                    and_(
                        PerformanceEntryModel.oa_user_id == UserModel.id,
                        PerformanceEntryModel.month == month,
                        PerformanceEntryModel.is_deleted == False,  # noqa: E712
                    ),
                )
                .where(*conditions)
                .order_by(UserModel.id.asc())
            )
        ).all()
        result: list[PerformanceEntryOutSchema] = []
        for user, profile, entry, group_name in rows:
            base = _money(profile.performance_base if profile else 0)
            deduction = _money(entry.deduction if entry else 0)
            result.append(
                PerformanceEntryOutSchema(
                    id=entry.id if entry else None,
                    oa_user_id=user.id,
                    user_name=user.name,
                    group_name=group_name,
                    month=month,
                    performance_base=float(base),
                    deduction=float(deduction),
                    performance_amount=float(max(base - deduction, Decimal("0"))),
                    remark=entry.remark if entry else None,
                    locked=locked,
                    attendance_ready=attendance_ready,
                    requires_performance=bool(profile and base > 0),
                    has_profile=profile is not None,
                    confirmed=bool(profile and base == 0) or bool(entry and entry.status == 1),
                    confirmed_at=entry.confirmed_at if entry else None,
                    confirmed_id=entry.confirmed_id if entry else None,
                )
            )
        return result

    async def _ensure_previous_period_editable(self, month: str) -> None:
        """3 日后才处理上月绩效；缺考勤时自动补拉上月。"""
        if month != self.previous_month():
            raise CustomException(msg="只能设置上一个自然月的绩效")
        if date.today().day < 3:
            raise CustomException(msg="每月 3 日 00:00 后才能设置上个月绩效")
        attendance = MonthlyReportService(self.auth, self.db, self.redis)
        if not await attendance.month_synced(month):
            result = await attendance.sync_month(month=month)
            if result.total == 0:
                raise CustomException(msg="没有已绑定钉钉且有考勤组的员工，无法拉取上月考勤")
        if not await attendance.month_synced(month):
            raise CustomException(msg="上月考勤同步后仍无数据，请先检查员工考勤组")

    async def save_performance(self, data: PerformanceEntrySaveSchema) -> PerformanceEntryOutSchema:
        self._require_manager()
        await self._ensure_previous_period_editable(data.month)
        binding = await self._group_binding(data.oa_user_id)
        if not binding:
            raise CustomException(msg="该员工没有考勤组，不参与薪酬计算")
        if await self.month_generated(data.month):
            raise CustomException(msg=f"{data.month} 工资条已生成，绩效扣款不能再修改")
        profile = (
            await self.db.execute(
                select(SalaryProfileModel).where(
                    SalaryProfileModel.oa_user_id == data.oa_user_id,
                    SalaryProfileModel.is_deleted == False,  # noqa: E712
                )
            )
        ).scalars().first()
        if not profile or profile.performance_base <= 0:
            raise CustomException(msg="该员工没有绩效奖金，无需设置月度绩效")
        entry = (
            await self.db.execute(
                select(PerformanceEntryModel).where(
                    PerformanceEntryModel.oa_user_id == data.oa_user_id,
                    PerformanceEntryModel.month == data.month,
                )
            )
        ).scalars().first()
        if entry and entry.status == 1:
            raise CustomException(msg="绩效已确认，请先撤销确认再修改")
        if entry:
            entry.deduction = _money(data.deduction)
            entry.remark = data.remark
            entry.is_deleted = False
            entry.updated_id = self.auth.user.id
        else:
            entry = PerformanceEntryModel(
                oa_user_id=data.oa_user_id,
                month=data.month,
                deduction=_money(data.deduction),
                remark=data.remark,
                created_id=self.auth.user.id,
            )
            self.db.add(entry)
        await self.db.flush()
        # 最后一份绩效设置完成时，私聊通知全员核对；成功去重，失败记录后可重试。
        await SalaryNotificationService(self.db, self.redis).notify_employees_for_review(data.month)
        base = _money(profile.performance_base if profile else 0)
        deduction = _money(data.deduction)
        return PerformanceEntryOutSchema(
            id=entry.id,
            oa_user_id=data.oa_user_id,
            user_name=binding.user.name if binding.user else None,
            group_name=binding.group_name,
            month=data.month,
            performance_base=float(base),
            deduction=float(deduction),
            performance_amount=float(max(base - deduction, Decimal("0"))),
            remark=data.remark,
            locked=False,
            attendance_ready=await MonthlyReportService(self.auth, self.db, self.redis).month_synced(data.month),
            requires_performance=True,
            confirmed=False,
        )

    async def _performance_progress(self, month: str) -> tuple[int, int]:
        """返回（应确认人数，已确认人数），只统计绩效基准大于 0 的员工。"""
        user_ids = (
            await self.db.execute(
                select(MonthlyReportModel.oa_user_id)
                .join(UserModel, UserModel.id == MonthlyReportModel.oa_user_id)
                .join(
                    SalaryProfileModel,
                    and_(
                        SalaryProfileModel.oa_user_id == MonthlyReportModel.oa_user_id,
                        SalaryProfileModel.is_deleted == False,  # noqa: E712
                        SalaryProfileModel.performance_base > 0,
                    ),
                )
                .where(
                    MonthlyReportModel.month == month,
                    MonthlyReportModel.group_id.isnot(None),
                    MonthlyReportModel.is_deleted == False,  # noqa: E712
                    UserModel.is_deleted == False,  # noqa: E712
                    UserModel.status == 0,
                )
            )
        ).scalars().all()
        if not user_ids:
            return 0, 0
        confirmed_count = (
            await self.db.execute(
                select(func.count(PerformanceEntryModel.id)).where(
                    PerformanceEntryModel.month == month,
                    PerformanceEntryModel.oa_user_id.in_(user_ids),
                    PerformanceEntryModel.status == 1,
                    PerformanceEntryModel.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar() or 0
        return len(set(user_ids)), confirmed_count

    async def confirm_performance(self, data: PerformanceConfirmSchema) -> PerformanceConfirmResultSchema:
        """逐人确认绩效；最后一人确认后立即自动生成整月工资条。"""
        self._require_manager()
        if await self.month_generated(data.month):
            raise CustomException(msg=f"{data.month} 工资条已生成，绩效确认状态不能修改")
        await self._ensure_previous_period_editable(data.month)
        if not await MonthlyReportService(self.auth, self.db, self.redis).month_synced(data.month):
            raise CustomException(msg=f"{data.month} 考勤尚未同步，请先拉取考勤再确认绩效")
        if not await self._group_binding(data.oa_user_id):
            raise CustomException(msg="该员工没有考勤组，不参与薪酬计算")
        if data.confirmed:
            # 「有绩效奖金」是确认动作的前提；撤销确认时不该再按当前档案拦一次，
            # 否则档案后来改成 0 绩效，之前已确认的记录就再也撤不回来（中等 7）。
            profile = (
                await self.db.execute(
                    select(SalaryProfileModel).where(
                        SalaryProfileModel.oa_user_id == data.oa_user_id,
                        SalaryProfileModel.is_deleted == False,  # noqa: E712
                    )
                )
            ).scalars().first()
            if not profile or profile.performance_base <= 0:
                raise CustomException(msg="该员工没有绩效奖金，无需确认月度绩效")
        entry = (
            await self.db.execute(
                select(PerformanceEntryModel).where(
                    PerformanceEntryModel.oa_user_id == data.oa_user_id,
                    PerformanceEntryModel.month == data.month,
                    PerformanceEntryModel.is_deleted == False,  # noqa: E712
                )
            )
        ).scalars().first()
        now = datetime.now(UTC)
        if data.confirmed:
            # ADR-0011 / BUG-04：有绩效员工即使扣款为 0，也必须先登记、再确认。
            # 不得在确认时静默补建 0 元记录，否则会绕过 save_performance 的设置
            # 流程与 N2 员工核对通知，并可能直接触发整月工资条生成。
            if not entry:
                raise CustomException(msg="请先登记该员工当月绩效（扣款为 0 也需登记），再确认")
            entry.status = 1
            entry.confirmed_at = now
            entry.confirmed_id = self.auth.user.id
            entry.updated_id = self.auth.user.id
        else:
            # ADR-0011 / 复核中等 7：撤销与确认必须同一口径 —— 记录存在、未软删、
            # 且原本确实已确认。否则明确报错，不能返回「已撤销」的假成功。
            if not entry or entry.status != 1:
                raise CustomException(msg="该员工当月绩效尚未确认，没有可撤销的确认")
            entry.status = 0
            entry.confirmed_at = None
            entry.confirmed_id = None
            entry.updated_id = self.auth.user.id
        await self.db.flush()

        total, confirmed_count = await self._performance_progress(data.month)
        all_confirmed = total > 0 and confirmed_count == total
        generated = 0
        if data.confirmed and all_confirmed:
            generation = await self.generate_month(month=data.month, source="auto")
            generated = generation.generated
            message = f"绩效已全部确认，已自动生成 {generated} 人工资条"
        elif data.confirmed:
            message = f"绩效已确认，当前进度 {confirmed_count}/{total}"
        else:
            message = f"已撤销绩效确认，当前进度 {confirmed_count}/{total}"
        return PerformanceConfirmResultSchema(
            month=data.month,
            confirmed_count=confirmed_count,
            total_count=total,
            all_confirmed=all_confirmed,
            payslips_generated=generated,
            message=message,
        )

    # ── 工资条生成与确认 ────────────────────────────────────────
    async def generation_blockers(self, month: str) -> list[str]:
        """整月生成前置检查：考勤是否拉全、是否已确认、应参与人员是否都有有效薪资档案。

        ADR-0012：无档案不等于零绩效员工。档案只要记录存在且未软删即为有效，
        绩效基准为 0 合法；缺任一人就整月不生成，并报出缺档案名单。
        ADR-0018：新增「该月考勤已确认」第二道闸 —— 考勤与休息日必须在人工确认
        全部登记完毕之后才允许生成；手动生成、每日任务、最后一人绩效确认后自动生成
        三条路径共用本方法。离职结算单不走这里（ADR-0016）。
        返回阻塞原因列表，空列表才允许生成。
        """
        attendance = MonthlyReportService(self.auth, self.db, self.redis)
        if not await attendance.month_synced(month):
            return [f"{month} 考勤尚未全量同步，整月工资条未生成"]
        if not await attendance.month_confirmed(month):
            return [f"{month} 考勤尚未确认，整月工资条未生成；请先到考勤统计页确认该月考勤（含公司休息日安排）"]
        rows = (
            await self.db.execute(
                select(MonthlyReportModel.oa_user_id, UserModel.name, MonthlyReportModel.name)
                .join(UserModel, UserModel.id == MonthlyReportModel.oa_user_id)
                .where(
                    MonthlyReportModel.month == month,
                    MonthlyReportModel.group_id.isnot(None),
                    MonthlyReportModel.is_deleted == False,  # noqa: E712
                    UserModel.is_deleted == False,  # noqa: E712
                    UserModel.status == 0,
                )
                .order_by(MonthlyReportModel.oa_user_id.asc())
            )
        ).all()
        user_ids = [row[0] for row in rows]
        if not user_ids:
            return [f"{month} 没有参与薪酬计算的考勤月报，整月工资条未生成"]
        profiled_ids = set(
            (
                await self.db.execute(
                    select(SalaryProfileModel.oa_user_id).where(
                        SalaryProfileModel.oa_user_id.in_(user_ids),
                        SalaryProfileModel.is_deleted == False,  # noqa: E712
                    )
                )
            )
            .scalars()
            .all()
        )
        missing: list[str] = []
        for oa_user_id, user_name, report_name in rows:
            if oa_user_id not in profiled_ids:
                missing.append(f"{report_name or user_name or '未命名'}（ID {oa_user_id}）")
        if missing:
            return [f"{month} 有 {len(missing)} 人未设置薪资档案，整月工资条未生成：{'、'.join(missing)}"]
        return []

    async def _rest_dates_by_group_id(self, month: str) -> dict[str, set[date]]:
        """按钉钉考勤组 ID 取当月登记的公司休息日日期集合。

        键只用 group_id，不再用 group_name 快照：钉钉改名、快照为空都不影响匹配。
        未软删且启用的记录才计入。
        """
        rows = (
            await self.db.execute(
                select(RestDayModel.group_id, RestDayModel.rest_date)
                .where(
                    RestDayModel.month == month,
                    RestDayModel.is_deleted == False,  # noqa: E712
                    RestDayModel.status == 0,
                    RestDayModel.group_id.isnot(None),
                )
            )
        ).all()
        result: dict[str, set[date]] = {}
        for group_id, rest_date in rows:
            gid = str(group_id or "").strip()
            if not gid:
                continue
            day = rest_date.date() if isinstance(rest_date, datetime) else rest_date
            result.setdefault(gid, set()).add(day)
        return result

    @staticmethod
    def _rest_allowance(
        report: MonthlyReportModel,
        policy_map: dict[str, RestDayGroupPolicy],
        rest_dates_by_group: dict[str, set[date]],
        raw_by_user_date: dict[tuple[int, date], str | None],
    ) -> Decimal:
        """「补工资」方案的当月补贴：逐天看当天钉钉原文，只有实际到岗才补。

        请假 / 旷工 / 无数据等未出勤的天不发补贴，也不改考勤统计（由考勤侧
        「不减应出勤」保证这些天仍按钉钉原值算请假、出勤自然少一天）。
        未配置该考勤组时返回 0，等同「没有公司休息日」，不报警。

        钉钉原文若「程序不认识」（other）一律抛错：钉钉一般不会改返回文案，
        改了就必须先更新程序，不能静默按「不发补贴」算过去。异常会让整批
        工资条生成失败（generate_month 整批原子），这正是强制暴露的意图。
        文本为空 / 没有日明细行（empty）是正常业务，不报错、只是不发补贴。
        """
        policy = policy_map.get(str(report.group_id or ""))
        if policy is None or policy.mode != 2 or policy.money is None:
            return Decimal("0")
        paid_days = 0
        for day in rest_dates_by_group.get(str(report.group_id or ""), set()):
            raw = raw_by_user_date.get((report.oa_user_id, day))
            category = _categorize_raw(raw)
            if category == "other":
                raise CustomException(
                    msg=f"钉钉考勤结果文案无法识别，请先更新程序再生成工资条: "
                    f"month={report.month} user={report.oa_user_id} date={day} 原文={raw!r}"
                )
            if category in PAID_REST_CATEGORIES:
                paid_days += 1
        return _money(policy.money * paid_days)

    @staticmethod
    def _group_required_days(rows: list[tuple[MonthlyReportModel, UserModel]]) -> dict[str, float]:
        """按考勤组取当月「应出勤」的全组标准值（ADR-0009 A4 / ADR-0011 第十一节）。

        钉钉按人按天返回列值，月中入职 / 未加入考勤组的人只覆盖在职后的日期，
        个人应出勤会被截短（2026-10 员工辛为 9，同组标准 23）。取同组当月应出勤的
        **众数**（并列取较大值）作为标准：正常员工的整月值天然是多数，月中入离职的
        截短值不会污染标准。只用于抬平偏低值，不改考勤月报，也不改 ADR-0009 的重算结果。
        众数至少要由两行共享才作数，避免并列时把 ADR-0009 休息日重算的合法差异
        （±1 天）误抬平、改动到正常员工的金额。
        """
        bucket: dict[str, list[float]] = {}
        for report, _ in rows:
            gid = str(report.group_id or "").strip()
            if not gid or report.required_days is None:
                continue
            bucket.setdefault(gid, []).append(float(report.required_days))
        result: dict[str, float] = {}
        for gid, values in bucket.items():
            counts = Counter(values)
            top = max(counts.values())
            if top < 2:
                logger.warning(f"工资条生成: 考勤组 {gid} 应出勤无明确众数({values})，不做全组抬平，需人工核对")
                continue
            result[gid] = max(value for value, count in counts.items() if count == top)
        return result

    @staticmethod
    def _calculate(
        report: MonthlyReportModel,
        profile: SalaryProfileModel | None,
        deduction: Decimal,
        rest_allowance: Decimal = Decimal("0"),
        *,
        required_override: float | None = None,
    ) -> dict[str, Any]:
        # ADR-0011 第十一节（2026-11-11 修订）：应出勤对全考勤组统一。
        # 若直接拿被截短的个人 R 当分母，自算 A 也同时被截短，A/R = 1，新人会拿整月工资。
        # 只把「低于全组标准」的个人 R 抬平；正常值与公司休息日重算后的值一律不动。
        personal_required = Decimal(str(report.required_days or 0))
        if required_override is None:
            required = personal_required
        else:
            required = max(personal_required, Decimal(str(required_override)))
        actual = Decimal(str(report.actual_days or 0))
        base_salary = _money(profile.base_salary if profile else 0)
        full_bonus = _money(profile.full_attendance_bonus if profile else 0)
        allowance = _money(profile.allowance if profile else 0)
        # 按考勤组方案算出来的当月休息日补贴，只进工资条、不改档案（ADR-0014）
        if rest_allowance > 0:
            allowance = _money(allowance + rest_allowance)
        performance_base = _money(profile.performance_base if profile else 0)
        performance_deduction = _money(deduction)

        # ADR-0011（2026-11-09 修订）：公司休息日正常上班会让 A > R（ADR-0009 A4 / ADR-0010）。
        # 用户拍板：折算基础工资不封顶，超出部分照算；全勤条件为 A >= R，仍发全额全勤奖。
        prorated_base = _money(base_salary * actual / required) if required > 0 else Decimal("0.00")
        full_award = full_bonus if required > 0 and actual >= required else Decimal("0.00")
        performance_amount = _money(max(performance_base - performance_deduction, Decimal("0")))
        missing_count = int(report.missing_checkin_count or 0) + int(report.missing_checkout_count or 0)
        missing_deduction = _money(Decimal(10 * missing_count * missing_count))
        gross = _money(prorated_base + full_award + allowance + performance_amount)
        net = _money(gross - missing_deduction)
        return {
            "required_days": float(required),
            "actual_days": float(actual),
            "missing_count": missing_count,
            "base_salary": base_salary,
            "full_attendance_bonus": full_bonus,
            "allowance": allowance,
            "performance_base": performance_base,
            "performance_deduction": performance_deduction,
            "prorated_base": prorated_base,
            "full_attendance_award": full_award,
            "performance_amount": performance_amount,
            "missing_deduction": missing_deduction,
            "gross_amount": gross,
            "net_amount": net,
        }

    async def generate_month(self, *, month: str, source: str) -> PayslipGenerationResultSchema:
        """整批生成某月工资条。任一人失败由请求/任务事务回滚整批。"""
        if await self.month_generated(month):
            return PayslipGenerationResultSchema(month=month, already_generated=True, messages=[f"{month} 已生成过，不能重复生成"])
        # ADR-0012：所有生成入口共用一道硬闸，缺档案 / 考勤未拉全都整月不生成。
        blockers = await self.generation_blockers(month)
        if blockers:
            raise CustomException(msg="；".join(blockers))
        rows = (
            await self.db.execute(
                select(MonthlyReportModel, UserModel)
                .join(UserModel, UserModel.id == MonthlyReportModel.oa_user_id)
                .where(
                    MonthlyReportModel.month == month,
                    MonthlyReportModel.group_id.isnot(None),
                    MonthlyReportModel.is_deleted == False,  # noqa: E712
                    UserModel.is_deleted == False,  # noqa: E712
                    UserModel.status == 0,
                )
                .order_by(MonthlyReportModel.oa_user_id.asc())
            )
        ).all()
        result = PayslipGenerationResultSchema(month=month)
        if not rows:
            result.messages.append(f"{month} 没有参与薪酬计算的考勤月报")
            return result

        user_ids = [report.oa_user_id for report, _ in rows]
        profiles = (
            (
                await self.db.execute(
                    select(SalaryProfileModel).where(
                        SalaryProfileModel.oa_user_id.in_(user_ids),
                        SalaryProfileModel.is_deleted == False,  # noqa: E712
                    )
                )
            )
            .scalars()
            .all()
        )
        profile_map = {profile.oa_user_id: profile for profile in profiles}
        entries = (
            (
                await self.db.execute(
                    select(PerformanceEntryModel).where(
                        PerformanceEntryModel.month == month,
                        PerformanceEntryModel.oa_user_id.in_(user_ids),
                        PerformanceEntryModel.is_deleted == False,  # noqa: E712
                    )
                )
            )
            .scalars()
            .all()
        )
        deduction_map = {entry.oa_user_id: entry.deduction for entry in entries}
        rest_dates_by_group = await self._rest_dates_by_group_id(month)
        policy_map = load_policy()
        daily_rows = (
            (
                await self.db.execute(
                    select(DailyReportModel).where(
                        DailyReportModel.month == month,
                        DailyReportModel.oa_user_id.in_(user_ids),
                        DailyReportModel.is_deleted == False,  # noqa: E712
                    )
                )
            )
            .scalars()
            .all()
        )
        raw_by_user_date: dict[tuple[int, date], str | None] = {}
        for row in daily_rows:
            day = row.work_date.date() if isinstance(row.work_date, datetime) else row.work_date
            raw_by_user_date[(row.oa_user_id, day)] = row.raw_text
        # ADR-0009 A4 / ADR-0011 第十一节：应出勤对全考勤组统一，抬平被钉钉截短的个人值。
        group_required = self._group_required_days(rows)
        now = datetime.now(UTC)
        for report, user in rows:
            gid = str(report.group_id or "").strip()
            required_override = group_required.get(gid)
            if required_override is not None and float(report.required_days or 0) < required_override:
                logger.warning(
                    f"工资条生成: 应出勤按考勤组统一抬平 group={gid} user={report.oa_user_id} "
                    f"钉钉个人值={report.required_days} → 全组标准={required_override}；"
                    f"实出勤仍取月报自算值 {report.actual_days}，钉钉漏记的到岗天数需人工核对"
                )
            profile = profile_map.get(report.oa_user_id)
            rest_allowance = self._rest_allowance(report, policy_map, rest_dates_by_group, raw_by_user_date)
            values = self._calculate(
                report,
                profile,
                deduction_map.get(report.oa_user_id, Decimal("0")),
                rest_allowance,
                required_override=required_override,
            )
            self.db.add(
                PayslipModel(
                    oa_user_id=report.oa_user_id,
                    month=month,
                    user_name=report.name or user.name,
                    dept_name=report.dept_name,
                    group_id=report.group_id,
                    group_name=report.group_name,
                    generation_source=source,
                    generated_at=now,
                    created_id=self.auth.user.id or None,
                    status=0,
                    **values,
                )
            )
            result.generated += 1
        await self.db.flush()
        return result

    async def confirm_payslip(self, *, id: int) -> PayslipOutSchema:
        self._require_superuser()
        row = (
            await self.db.execute(
                select(PayslipModel).where(PayslipModel.id == id, PayslipModel.is_deleted == False)  # noqa: E712
            )
        ).scalars().first()
        if not row:
            raise CustomException(msg="工资条不存在")
        # ADR-0016 第二节：结算单落库即锁定，绝不能被超管确认动作改成正式工资条，
        # 更不能给已离职的人发「工资已发放」钉钉通知。
        if row.generation_source == DISMISSAL_SOURCE:
            raise CustomException(msg="离职结算单不参与超管确认，落库即锁定")
        if row.status == 1:
            raise CustomException(msg="工资条已确认，不能重复确认或撤销")
        row.status = 1
        row.confirmed_at = datetime.now(UTC)
        row.confirmed_id = self.auth.user.id
        row.updated_id = self.auth.user.id
        await self.db.flush()
        await SalaryNotificationService(self.db, self.redis).notify_payslip_paid(
            oa_user_id=row.oa_user_id,
            month=row.month,
            name=row.user_name or "同事",
        )
        return PayslipOutSchema.model_validate(row)

    # ── 服务端业务日历（ADR-0017）────────────────────────────────
    @staticmethod
    def business_calendar() -> BusinessCalendarSchema:
        """服务器日期、上个月与「3 日后开放」统一由服务端给出，前端不再自己用本地日期算。"""
        today = date.today()
        return BusinessCalendarSchema(
            today=today.isoformat(),
            current_month=today.strftime("%Y-%m"),
            previous_month=(today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m"),
            performance_open=today.day >= 3,
        )

    # ── 离职结算单（ADR-0016）────────────────────────────────────
    async def list_dismissal_payslips(self, *, month: str) -> list[PayslipOutSchema]:
        """按月份查离职结算单，供工资条页「查看离职结算单」按钮用（管理员/超管）。"""
        self._require_manager()
        rows = (
            await self.db.execute(
                select(PayslipModel)
                .where(
                    PayslipModel.month == month,
                    PayslipModel.generation_source == DISMISSAL_SOURCE,
                    PayslipModel.is_deleted == False,  # noqa: E712
                )
                .order_by(PayslipModel.oa_user_id.asc(), PayslipModel.month.asc())
            )
        ).scalars().all()
        return [PayslipOutSchema.model_validate(row) for row in rows]

    async def _settlement_allowance_context(self, *, months: list[str], user_ids: list[int]) -> dict[str, dict[str, Any]]:
        """按月份备齐公司休息日补贴计算所需的日明细原文（ADR-0014），避免逐人重复查库。"""
        policy_map = load_policy()
        context: dict[str, dict[str, Any]] = {}
        for month in months:
            rest_dates_by_group = await self._rest_dates_by_group_id(month)
            daily_rows = (
                await self.db.execute(
                    select(DailyReportModel).where(
                        DailyReportModel.month == month,
                        DailyReportModel.oa_user_id.in_(user_ids),
                        DailyReportModel.is_deleted == False,  # noqa: E712
                    )
                )
            ).scalars().all()
            raw_by_user_date: dict[tuple[int, date], str | None] = {}
            for row in daily_rows:
                day = row.work_date.date() if isinstance(row.work_date, datetime) else row.work_date
                raw_by_user_date[(row.oa_user_id, day)] = row.raw_text
            context[month] = {
                "policy_map": policy_map,
                "rest_dates_by_group": rest_dates_by_group,
                "raw_by_user_date": raw_by_user_date,
            }
        return context

    async def _dismissal_plan(
        self, *, oa_user_ids: list[int], deduction_map: dict[int, Decimal]
    ) -> tuple[str, str, list[_DismissalPlan]]:
        """预览与落库共用的唯一计算入口。

        判据只有「删除时上月是否已有正式工资条」（ADR-0016 第四节）：
        - 已有 → 只出离职当月 1 张，管理员手填的扣款落在这张；
        - 没有 → 出上月 + 离职当月 2 张，扣款落在上月，离职当月绩效全部扣除清零。
        半个月 / 离职日只作展示背景，不进代码分支。
        """
        ids = sorted(set(oa_user_ids))
        current_month = date.today().strftime("%Y-%m")
        previous_month = self.previous_month()
        months = [previous_month, current_month]
        if not ids:
            return current_month, previous_month, []

        users = (await self.db.execute(select(UserModel).where(UserModel.id.in_(ids)))).scalars().all()
        user_map = {user.id: user for user in users}
        profiles = (
            await self.db.execute(
                select(SalaryProfileModel).where(
                    SalaryProfileModel.oa_user_id.in_(ids),
                    SalaryProfileModel.is_deleted == False,  # noqa: E712
                )
            )
        ).scalars().all()
        profile_map = {profile.oa_user_id: profile for profile in profiles}

        # 唯一约束是 (oa_user_id, month)，连软删行也会占位，所以这里不按 is_deleted 过滤：
        # 4601 重试、重复提交都要幂等跳过。
        existing_rows = (
            await self.db.execute(
                select(PayslipModel.oa_user_id, PayslipModel.month, PayslipModel.generation_source).where(
                    PayslipModel.oa_user_id.in_(ids),
                    PayslipModel.month.in_(months),
                )
            )
        ).all()
        existing: dict[tuple[int, str], str] = {(row[0], row[1]): row[2] for row in existing_rows}

        reports = (
            await self.db.execute(
                select(MonthlyReportModel).where(
                    MonthlyReportModel.oa_user_id.in_(ids),
                    MonthlyReportModel.month.in_(months),
                    MonthlyReportModel.group_id.isnot(None),
                    MonthlyReportModel.is_deleted == False,  # noqa: E712
                )
            )
        ).scalars().all()
        report_map = {(report.oa_user_id, report.month): report for report in reports}
        allowance_context = await self._settlement_allowance_context(months=months, user_ids=ids)

        plans: list[_DismissalPlan] = []
        for uid in ids:
            user = user_map.get(uid)
            if not user:
                continue
            profile = profile_map.get(uid)
            performance_base = _money(profile.performance_base if profile else 0)
            deduction = _money(deduction_map.get(uid, 0))
            previous_source = existing.get((uid, previous_month))
            previous_payslip_exists = bool(previous_source and previous_source != DISMISSAL_SOURCE)
            target_months = [current_month] if previous_payslip_exists else months
            messages: list[str] = []
            if not profile:
                messages.append("未设置薪资档案，金额按 0 计算")

            month_plans: list[_DismissalMonthPlan] = []
            for month in months:
                is_current = month == current_month
                report = report_map.get((uid, month))
                source = existing.get((uid, month))
                will_generate = month in target_months
                reason: str | None = None
                if not will_generate:
                    reason = "上月已有正式工资条，不重复出单" if month == previous_month and previous_payslip_exists else "该月已有工资凭证"
                elif source:
                    will_generate = False
                    reason = "该月已有离职结算单" if source == DISMISSAL_SOURCE else "该月已有正式工资条"
                elif report is None:
                    will_generate = False
                    # ADR-0016 第三节：预览与落库共用此文案，保持中性。
                    # 实时拉取发生在确认删除时，这里不能下「未生成结算单」的终局判断。
                    reason = "暂无月报"
                if not will_generate:
                    month_plans.append(_DismissalMonthPlan(month, is_current, report, Decimal("0"), False, reason, None))
                    if reason:
                        messages.append(f"{month}：{reason}")
                    continue

                # 两张单时离职当月绩效全部扣除清零：performance_deduction = 绩效基准，实际绩效 0。
                month_deduction = performance_base if not previous_payslip_exists and is_current else deduction
                ctx = allowance_context[month]
                rest_allowance = self._rest_allowance(report, ctx["policy_map"], ctx["rest_dates_by_group"], ctx["raw_by_user_date"])
                values = self._calculate(report, profile, month_deduction, rest_allowance)
                month_plans.append(_DismissalMonthPlan(month, is_current, report, month_deduction, True, None, values))
            plans.append(
                _DismissalPlan(
                    user=user,
                    profile=profile,
                    previous_payslip_exists=previous_payslip_exists,
                    deduction=deduction,
                    months=month_plans,
                    messages=messages,
                )
            )
        return current_month, previous_month, plans

    async def dismissal_context(self, *, oa_user_ids: list[int]) -> DismissalContextResultSchema:
        """删除员工弹窗只需逐人填扣款的上下文，不做任何结算预览（ADR-0016 12:41 A1）。

        只读 sys_user 与薪资档案：姓名、绩效基准、是否零绩效。不读月报、不算金额、
        不预判出单月份；确认删除后才由删除链实时拉考勤并落结算单
        （ADR-0016 第三节 / ADR-0007 第四节）。
        """
        ids = sorted(set(oa_user_ids))
        items: list[DismissalEmployeeContextSchema] = []
        if not ids:
            return DismissalContextResultSchema(tip=DISMISSAL_TIP, items=items)

        users = (await self.db.execute(select(UserModel).where(UserModel.id.in_(ids)))).scalars().all()
        profiles = (
            await self.db.execute(
                select(SalaryProfileModel).where(
                    SalaryProfileModel.oa_user_id.in_(ids),
                    SalaryProfileModel.is_deleted == False,  # noqa: E712
                )
            )
        ).scalars().all()
        profile_map = {profile.oa_user_id: profile for profile in profiles}
        for user in sorted(users, key=lambda item: item.id):
            profile = profile_map.get(user.id)
            base = _money(profile.performance_base if profile else 0)
            items.append(
                DismissalEmployeeContextSchema(
                    oa_user_id=user.id,
                    user_name=user.name,
                    performance_base=float(base),
                    requires_performance=base > 0,
                )
            )
        return DismissalContextResultSchema(tip=DISMISSAL_TIP, items=items)

    async def generate_dismissal_settlements(
        self, *, oa_user_ids: list[int], deductions: list[DismissalDeductionSchema]
    ) -> DismissalSettlementResultSchema:
        """删除链内部调用：在钉钉离职之前落库，落库即锁定、只读。

        返回生成张数与仍缺月报的月份（ADR-0016 12:41 A3），供删除响应 msg 告知管理员。
        不检查薪资管理权限 —— 调用方是已经过鉴权的 `DELETE /system/user/delete` 删除链。
        """
        wanted = set(oa_user_ids)
        deduction_map = {item.oa_user_id: _money(item.deduction) for item in deductions if item.oa_user_id in wanted}
        _, _, plans = await self._dismissal_plan(oa_user_ids=oa_user_ids, deduction_map=deduction_map)
        now = datetime.now(UTC)
        result = DismissalSettlementResultSchema()
        for plan in plans:
            for month_plan in plan.months:
                if not month_plan.will_generate or not month_plan.report or not month_plan.values:
                    # A3：只把「实时拉取后仍无月报」的月份告知管理员；幂等跳过、
                    # 上月已有正式工资条都属正常，不打扰。
                    if month_plan.reason == "暂无月报":
                        result.skipped.append(f"{plan.user.name} {month_plan.month} {month_plan.reason}，未生成结算单")
                    continue
                report = month_plan.report
                self.db.add(
                    PayslipModel(
                        oa_user_id=plan.user.id,
                        month=month_plan.month,
                        user_name=report.name or plan.user.name,
                        dept_name=report.dept_name,
                        group_id=report.group_id,
                        group_name=report.group_name,
                        generation_source=DISMISSAL_SOURCE,
                        generated_at=now,
                        created_id=self.auth.user.id or None,
                        # ADR-0016（2026-11-09 杜总追加）：结算单没有确认流程，落库即终态。
                        # status=1 让「终态凭证」数据自洽，也给 confirm_payslip 多一道硬保险。
                        status=1,
                        confirmed_at=now,
                        confirmed_id=self.auth.user.id or None,
                        **month_plan.values,
                    )
                )
                result.generated_count += 1
        await self.db.flush()
        if result.generated_count:
            logger.info(f"离职结算单已生成: users={sorted(wanted)} 张数={result.generated_count}")
        return result

    async def process_daily_notifications(self) -> dict[str, Any]:
        """3 日起每日提醒；设置完成则通知员工，并重试此前失败的员工通知。"""
        month = self.previous_month()
        if date.today().day < 3:
            return {"admin": {}, "review": {}, "generated_without_performance": 0, "blocked_reasons": [], "retry": {}}
        notifications = SalaryNotificationService(self.db, self.redis)
        admin = await notifications.notify_admins_if_pending(month)
        review = await notifications.notify_employees_for_review(month)
        # 全员零绩效时没有管理员确认动作，设置范围天然完成；但必须先过硬闸，
        # 否则「无档案」也会被当成零绩效，在考勤没拉全时生成并锁定 0 元工资条。
        total, _ = await self._performance_progress(month)
        generated = 0
        blocked_reasons: list[str] = []
        if total == 0 and not await self.pending_positive_setting_count(month) and not await self.month_generated(month):
            blockers = await self.generation_blockers(month)
            if blockers:
                blocked_reasons = blockers
                # 不让异常打断每日任务，否则同事务里的通知日志会一起回滚。
                logger.warning(f"{month} 工资条未自动生成: {'；'.join(blockers)}")
            else:
                generated = (await self.generate_month(month=month, source="auto")).generated
        retry = await notifications.retry_failed_employee_notifications()
        return {
            "admin": admin,
            "review": review,
            "generated_without_performance": generated,
            "blocked_reasons": blocked_reasons,
            "retry": retry,
        }

    async def pending_positive_setting_count(self, month: str) -> int:
        return len(await SalaryNotificationService(self.db, self.redis).pending_setting_user_ids(month))
