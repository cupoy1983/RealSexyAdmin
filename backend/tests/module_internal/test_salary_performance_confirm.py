"""BUG-04：绩效确认不得绕过「先登记再确认」，也不得跳过 N2 员工核对通知。

复现原问题：员工有绩效基准但当月未登记时，旧 confirm_performance 会在
confirmed=True 分支静默补建一条 deduction=0 / status=1 的记录；若他是最后
一个待确认者，还会立即生成整月工资条，而这条路径从未调用 save_performance
触发的 N2 员工核对通知。修复后必须先登记，确认时拒绝补建。
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import AuthSchema, CoreUserSchema
from app.core.database import async_db_session
from app.core.exceptions import CustomException
from app.modules.system.user.model import UserModel
from app.plugin.module_internal.account.model import ExternalAccountBindingModel
from app.plugin.module_internal.account.schema import SYSTEM_DINGTALK
from app.plugin.module_internal.attendance.model import MonthlyReportModel
from app.plugin.module_internal.attendance.service import MonthlyReportService
from app.plugin.module_internal.salary.model import (
    PayslipModel,
    PerformanceEntryModel,
    SalaryProfileModel,
)
from app.plugin.module_internal.salary.notification import SalaryNotificationService
from app.plugin.module_internal.salary.schema import PerformanceConfirmSchema, PerformanceEntrySaveSchema
from app.plugin.module_internal.salary.service import SalaryService

MONTH = "2026-07"


async def _noop(*args, **kwargs) -> None:
    return None


async def _always_synced(self, month: str) -> bool:
    return True


async def _seed_staff(db: AsyncSession, username: str, seq: int) -> UserModel:
    """造一个「有钉钉考勤组 + 当月月报 + 绩效基准」的员工。"""
    user = UserModel(username=username, name=username, password="x", status=0, is_deleted=False)
    db.add(user)
    await db.flush()
    db.add(
        ExternalAccountBindingModel(
            system_type=SYSTEM_DINGTALK,
            oa_user_id=user.id,
            external_user_id=f"bug04_ding_{seq}",
            mobile=f"139{seq:08d}"[:11],
            group_id="G-BUG04",
            group_name="缺陷测试组",
            status=0,
            is_deleted=False,
        )
    )
    db.add(
        MonthlyReportModel(
            oa_user_id=user.id,
            month=MONTH,
            group_id="G-BUG04",
            status=0,
            is_deleted=False,
        )
    )
    db.add(
        SalaryProfileModel(
            oa_user_id=user.id,
            base_salary=5000,
            full_attendance_bonus=0,
            allowance=0,
            performance_base=1000,
            status=0,
            is_deleted=False,
        )
    )
    await db.flush()
    return user


def _service(db: AsyncSession) -> SalaryService:
    auth = AuthSchema(user=CoreUserSchema(id=990401, is_superuser=True))
    return SalaryService(auth, db)


async def test_confirm_requires_prior_entry(test_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """未登记直接确认：必须拒绝，且不产生绩效记录、不生成工资条。"""
    monkeypatch.setattr(SalaryService, "_ensure_previous_period_editable", _noop)
    monkeypatch.setattr(MonthlyReportService, "month_synced", _always_synced)
    monkeypatch.setattr(SalaryNotificationService, "notify_employees_for_review", _noop)

    async with async_db_session() as db:
        user_a = await _seed_staff(db, "bug04_user_a", 401)
        await _seed_staff(db, "bug04_user_b", 402)
        service = _service(db)

        with pytest.raises(CustomException, match="请先登记"):
            await service.confirm_performance(
                PerformanceConfirmSchema(oa_user_id=user_a.id, month=MONTH, confirmed=True)
            )

        entry_count = (
            await db.execute(
                select(func.count(PerformanceEntryModel.id)).where(
                    PerformanceEntryModel.month == MONTH,
                    PerformanceEntryModel.oa_user_id == user_a.id,
                )
            )
        ).scalar()
        payslip_count = (
            await db.execute(select(func.count(PayslipModel.id)).where(PayslipModel.month == MONTH))
        ).scalar()
        assert entry_count == 0, "确认时不得静默补建 0 元绩效记录"
        assert payslip_count == 0, "不得因绕过登记而触发生成整月工资条"


async def test_confirm_after_zero_deduction_registration(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """先登记 0 元绩效（触发 N2 核对），再确认：正常通过且不误生成工资条。"""
    notified: list[str] = []

    async def _record_notification(self, month: str) -> None:
        notified.append(month)

    monkeypatch.setattr(SalaryService, "_ensure_previous_period_editable", _noop)
    monkeypatch.setattr(MonthlyReportService, "month_synced", _always_synced)
    monkeypatch.setattr(SalaryNotificationService, "notify_employees_for_review", _record_notification)

    async with async_db_session() as db:
        user_a = await _seed_staff(db, "bug04_user_c", 403)
        await _seed_staff(db, "bug04_user_d", 404)
        service = _service(db)

        saved = await service.save_performance(
            PerformanceEntrySaveSchema(oa_user_id=user_a.id, month=MONTH, deduction=0, remark="无扣款")
        )
        assert saved.id is not None
        assert notified == [MONTH], "登记绩效时必须触发 N2 员工核对通知"

        result = await service.confirm_performance(
            PerformanceConfirmSchema(oa_user_id=user_a.id, month=MONTH, confirmed=True)
        )
        assert result.confirmed_count == 1
        assert result.total_count == 2
        assert result.all_confirmed is False
        assert result.payslips_generated == 0

        entry = (
            await db.execute(
                select(PerformanceEntryModel).where(
                    PerformanceEntryModel.oa_user_id == user_a.id,
                    PerformanceEntryModel.month == MONTH,
                )
            )
        ).scalars().one()
        assert entry.status == 1
        assert entry.confirmed_at is not None


# ── 复核中等 7：撤销绩效确认必须与确认同一口径 ────────────────────


def _patch_common(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SalaryService, "_ensure_previous_period_editable", _noop)
    monkeypatch.setattr(MonthlyReportService, "month_synced", _always_synced)


async def _entry_for(db: AsyncSession, oa_user_id: int) -> PerformanceEntryModel | None:
    """查不带 is_deleted 条件，才能验证软删行有没有被误改。"""
    return (
        await db.execute(
            select(PerformanceEntryModel).where(
                PerformanceEntryModel.oa_user_id == oa_user_id,
                PerformanceEntryModel.month == MONTH,
            )
        )
    ).scalars().first()


async def test_revoke_rejects_soft_deleted_entry(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """软删的绩效登记不得被当成「可撤销」改掉，更不能返回「已撤销」假成功。"""
    _patch_common(monkeypatch)
    async with async_db_session() as db:
        user = await _seed_staff(db, "bug07_soft", 501)
        db.add(
            PerformanceEntryModel(
                oa_user_id=user.id,
                month=MONTH,
                deduction=0,
                status=1,
                confirmed_at=datetime.now(UTC),
                confirmed_id=990401,
                is_deleted=True,
            )
        )
        await db.flush()

        with pytest.raises(CustomException, match="没有可撤销的确认"):
            await _service(db).confirm_performance(
                PerformanceConfirmSchema(oa_user_id=user.id, month=MONTH, confirmed=False)
            )

        entry = await _entry_for(db, user.id)
        assert entry is not None and bool(entry.is_deleted) is True
        assert entry.status == 1 and entry.confirmed_at is not None, "软删行不得被撤销分支改写"


async def test_revoke_requires_existing_confirmation(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没有登记、或登记了但从未确认，撤销都必须明确报错。"""
    _patch_common(monkeypatch)
    async with async_db_session() as db:
        user = await _seed_staff(db, "bug07_none", 502)
        service = _service(db)
        payload = PerformanceConfirmSchema(oa_user_id=user.id, month=MONTH, confirmed=False)

        with pytest.raises(CustomException, match="没有可撤销的确认"):
            await service.confirm_performance(payload)

        db.add(
            PerformanceEntryModel(
                oa_user_id=user.id, month=MONTH, deduction=10, status=0, is_deleted=False
            )
        )
        await db.flush()
        with pytest.raises(CustomException, match="没有可撤销的确认"):
            await service.confirm_performance(payload)

        entry = await _entry_for(db, user.id)
        assert entry is not None and entry.status == 0 and entry.confirmed_at is None


async def test_revoke_still_works_after_profile_performance_base_becomes_zero(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """档案后来改成 0 绩效，不能把之前已确认的记录卡死撤不回来。"""
    _patch_common(monkeypatch)
    async with async_db_session() as db:
        user = await _seed_staff(db, "bug07_zero", 503)
        db.add(
            PerformanceEntryModel(
                oa_user_id=user.id,
                month=MONTH,
                deduction=0,
                status=1,
                confirmed_at=datetime.now(UTC),
                confirmed_id=990401,
                is_deleted=False,
            )
        )
        profile = (
            await db.execute(
                select(SalaryProfileModel).where(SalaryProfileModel.oa_user_id == user.id)
            )
        ).scalars().one()
        profile.performance_base = 0
        await db.flush()

        result = await _service(db).confirm_performance(
            PerformanceConfirmSchema(oa_user_id=user.id, month=MONTH, confirmed=False)
        )
        assert "已撤销" in result.message
        entry = await _entry_for(db, user.id)
        assert entry is not None
        assert entry.status == 0
        assert entry.confirmed_at is None and entry.confirmed_id is None
