from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio.client import Redis

from app.core.ap_scheduler import SchedulerUtil
from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.exceptions import CustomException
from app.modules.system.user.model import UserModel
from app.plugin.module_internal.account.model import ExternalAccountBindingModel
from app.plugin.module_internal.account.schema import SYSTEM_DINGTALK
from app.plugin.module_internal.attendance.model import MonthlyReportModel
from app.plugin.module_internal.attendance.service import MonthlyReportService

from .model import PayslipModel, PerformanceEntryModel, SalaryProfileModel
from .notification import SalaryNotificationService
from .schema import (
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
_MONEY_STEP = Decimal("0.01")


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
        conditions: list[Any] = [PayslipModel.is_deleted == False]  # noqa: E712
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
        count = (
            await self.db.execute(
                select(func.count(PayslipModel.id)).where(
                    PayslipModel.month == month,
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
                )
            )
        ).scalars().first()
        now = datetime.now(UTC)
        if data.confirmed:
            if not entry:
                entry = PerformanceEntryModel(
                    oa_user_id=data.oa_user_id,
                    month=data.month,
                    deduction=Decimal("0.00"),
                    created_id=self.auth.user.id,
                )
                self.db.add(entry)
            entry.status = 1
            entry.is_deleted = False
            entry.confirmed_at = now
            entry.confirmed_id = self.auth.user.id
            entry.updated_id = self.auth.user.id
        elif entry:
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
    @staticmethod
    def _calculate(report: MonthlyReportModel, profile: SalaryProfileModel | None, deduction: Decimal) -> dict[str, Any]:
        required = Decimal(str(report.required_days or 0))
        actual = Decimal(str(report.actual_days or 0))
        base_salary = _money(profile.base_salary if profile else 0)
        full_bonus = _money(profile.full_attendance_bonus if profile else 0)
        allowance = _money(profile.allowance if profile else 0)
        performance_base = _money(profile.performance_base if profile else 0)
        performance_deduction = _money(deduction)

        prorated_base = _money(base_salary * actual / required) if required > 0 else Decimal("0.00")
        full_award = full_bonus if required > 0 and actual == required else Decimal("0.00")
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
        now = datetime.now(UTC)
        missing_profiles = 0
        for report, user in rows:
            profile = profile_map.get(report.oa_user_id)
            if not profile:
                missing_profiles += 1
            values = self._calculate(report, profile, deduction_map.get(report.oa_user_id, Decimal("0")))
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
        if missing_profiles:
            result.messages.append(f"{missing_profiles} 人未设置薪资档案，相关薪资项目按 0 元计算")
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

    async def process_daily_notifications(self) -> dict[str, Any]:
        """3 日起每日提醒；设置完成则通知员工，并重试此前失败的员工通知。"""
        month = self.previous_month()
        if date.today().day < 3:
            return {"admin": {}, "review": {}, "generated_without_performance": 0, "retry": {}}
        notifications = SalaryNotificationService(self.db, self.redis)
        admin = await notifications.notify_admins_if_pending(month)
        review = await notifications.notify_employees_for_review(month)
        # 全员零绩效时没有管理员确认动作，设置范围天然完成，直接生成工资条。
        total, _ = await self._performance_progress(month)
        generated = 0
        if total == 0 and not await self.pending_positive_setting_count(month) and not await self.month_generated(month):
            generated = (await self.generate_month(month=month, source="auto")).generated
        retry = await notifications.retry_failed_employee_notifications()
        return {"admin": admin, "review": review, "generated_without_performance": generated, "retry": retry}

    async def pending_positive_setting_count(self, month: str) -> int:
        return len(await SalaryNotificationService(self.db, self.redis).pending_setting_user_ids(month))
