"""复核严重 3：无薪资档案不得被当成零绩效员工。

覆盖用户 A1（整月阻止 + 报出缺档案名单）、A2（档案记录存在且未软删即有效，
绩效基准 0 合法）、每日任务预检不生成，以及「无档案 / 零绩效」在绩效列表里必须区分。
"""

from datetime import date

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
from app.plugin.module_internal.salary.model import PayslipModel, SalaryProfileModel
from app.plugin.module_internal.salary.notification import SalaryNotificationService
from app.plugin.module_internal.salary.schema import PerformanceConfirmSchema, PerformanceEntrySaveSchema
from app.plugin.module_internal.salary.service import SalaryService

# 避开其他测试文件已占用的月份：test_attendance_sync_state.py 会提交 2026-05 的月报，
# 共用月份会让本文件的「全员档案完整」用例读到残留人员，被硬闸正确拦下但断言失真。
MONTH = "2040-03"
GROUP_ID = "G-SEV3"


async def _always_synced(self, month: str) -> bool:
    return True


async def _always_confirmed(self, month: str) -> bool:
    """ADR-0018 新增的考勤确认闸：本文件只验证「缺薪资档案」这一道闸，
    固定视为该月考勤已确认；确认闸本身由 test_attendance_month_confirm.py 覆盖。
    """
    return True


async def _never_synced(self, month: str) -> bool:
    return False


async def _no_notifications(self, *args, **kwargs) -> dict[str, int]:
    return {"sent": 0, "failed": 0}


class _FixedToday:
    """把「今天」固定在 2040-04-15，使 previous_month() == MONTH。"""

    @staticmethod
    def today() -> date:
        return date(2040, 4, 15)


@pytest.fixture(autouse=True)
def _isolated_policy_and_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_REST_DAY_POLICY", "{}")
    monkeypatch.setattr(MonthlyReportService, "month_synced", _always_synced)
    monkeypatch.setattr(MonthlyReportService, "month_confirmed", _always_confirmed)


async def _seed_staff(
    db: AsyncSession,
    username: str,
    seq: int,
    *,
    with_profile: bool,
    performance_base: float = 0,
) -> UserModel:
    user = UserModel(username=username, name=username, password="x", status=0, is_deleted=False)
    db.add(user)
    await db.flush()
    db.add(
        ExternalAccountBindingModel(
            system_type=SYSTEM_DINGTALK,
            oa_user_id=user.id,
            external_user_id=f"sev3_ding_{seq}",
            mobile=f"137{seq:08d}"[:11],
            group_id=GROUP_ID,
            group_name="严重3测试组",
            status=0,
            is_deleted=False,
        )
    )
    db.add(
        MonthlyReportModel(
            oa_user_id=user.id,
            month=MONTH,
            group_id=GROUP_ID,
            group_name="严重3测试组",
            name=username,
            status=0,
            is_deleted=False,
        )
    )
    if with_profile:
        db.add(
            SalaryProfileModel(
                oa_user_id=user.id,
                base_salary=5000,
                full_attendance_bonus=0,
                allowance=0,
                performance_base=performance_base,
                status=0,
                is_deleted=False,
            )
        )
    await db.flush()
    return user


def _service(db: AsyncSession) -> SalaryService:
    return SalaryService(AuthSchema(user=CoreUserSchema(id=990601, is_superuser=True)), db)


async def _payslip_count(db: AsyncSession, month: str = MONTH) -> int:
    return (
        await db.execute(
            select(func.count(PayslipModel.id)).where(
                PayslipModel.month == month,
                PayslipModel.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar() or 0


async def test_all_missing_profiles_blocks_whole_month(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """全员无档案：整月不生成，异常消息带全部缺档案姓名与 ID。"""
    async with async_db_session() as db:
        user_a = await _seed_staff(db, "sev3_all_a", 601, with_profile=False)
        user_b = await _seed_staff(db, "sev3_all_b", 602, with_profile=False)

        with pytest.raises(CustomException, match="未设置薪资档案") as exc:
            await _service(db).generate_month(month=MONTH, source="auto")

        message = str(exc.value)
        assert "sev3_all_a" in message and "sev3_all_b" in message
        assert str(user_a.id) in message and str(user_b.id) in message
        assert await _payslip_count(db) == 0, "不得生成任何 0 元工资条并锁死月份"


async def test_one_missing_profile_blocks_entire_month(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只缺一人也要整月阻止：不能给有档案者单独生成，否则其余人永远无法补生成。"""
    async with async_db_session() as db:
        await _seed_staff(db, "sev3_mix_ok", 603, with_profile=True, performance_base=0)
        await _seed_staff(db, "sev3_mix_missing", 604, with_profile=False)

        with pytest.raises(CustomException, match="未设置薪资档案") as exc:
            await _service(db).generate_month(month=MONTH, source="auto")

        message = str(exc.value)
        assert "sev3_mix_missing" in message
        assert "sev3_mix_ok" not in message
        assert await _payslip_count(db) == 0


async def test_attendance_not_synced_blocks_generation(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """考勤没拉全时，即使全员都有档案也不生成。"""
    monkeypatch.setattr(MonthlyReportService, "month_synced", _never_synced)
    async with async_db_session() as db:
        await _seed_staff(db, "sev3_unsynced", 605, with_profile=True)

        with pytest.raises(CustomException, match="考勤尚未全量同步"):
            await _service(db).generate_month(month=MONTH, source="auto")
        assert await _payslip_count(db) == 0


async def test_zero_performance_profiles_are_valid(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A2：绩效基准 0 是合法档案，零绩效员工仍应正常生成工资条。"""
    async with async_db_session() as db:
        await _seed_staff(db, "sev3_zero_a", 606, with_profile=True, performance_base=0)
        await _seed_staff(db, "sev3_zero_b", 607, with_profile=True, performance_base=0)

        result = await _service(db).generate_month(month=MONTH, source="auto")
        assert result.generated == 2
        assert await _payslip_count(db) == 2


async def test_daily_task_reports_blockers_without_generating(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """每日 12:00 任务：预检到缺档案就跳过生成，返回名单，不抛异常回滚通知。"""
    monkeypatch.setattr("app.plugin.module_internal.salary.service.date", _FixedToday)
    monkeypatch.setattr(SalaryNotificationService, "notify_admins_if_pending", _no_notifications)
    monkeypatch.setattr(SalaryNotificationService, "notify_employees_for_review", _no_notifications)
    monkeypatch.setattr(SalaryNotificationService, "retry_failed_employee_notifications", _no_notifications)

    async with async_db_session() as db:
        await _seed_staff(db, "sev3_daily_a", 608, with_profile=False)
        await _seed_staff(db, "sev3_daily_b", 609, with_profile=False)

        result = await _service(db).process_daily_notifications()
        assert result["generated_without_performance"] == 0
        assert len(result["blocked_reasons"]) == 1
        assert "sev3_daily_a" in result["blocked_reasons"][0]
        assert "sev3_daily_b" in result["blocked_reasons"][0]
        assert await _payslip_count(db) == 0


async def test_performance_list_distinguishes_missing_profile_from_zero_performance(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """前端状态文案的依据：同样 requires_performance=False，has_profile 必须能区分两者。"""
    async with async_db_session() as db:
        missing = await _seed_staff(db, "sev3_list_missing", 610, with_profile=False)
        zero = await _seed_staff(db, "sev3_list_zero", 611, with_profile=True, performance_base=0)

        rows = await _service(db).list_performance(month=MONTH)
        by_id = {row.oa_user_id: row for row in rows}
        assert by_id[missing.id].has_profile is False
        assert by_id[missing.id].requires_performance is False
        assert by_id[zero.id].has_profile is True
        assert by_id[zero.id].requires_performance is False


async def test_confirm_path_also_blocked_by_missing_profile(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """最后一人确认触发的自动生成同样受硬闸保护，不能绕过每日任务单独生成。"""
    async def _noop(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(SalaryService, "_ensure_previous_period_editable", _noop)
    monkeypatch.setattr(SalaryNotificationService, "notify_employees_for_review", _noop)

    async with async_db_session() as db:
        staff = await _seed_staff(db, "sev3_confirm_ok", 612, with_profile=True, performance_base=1000)
        await _seed_staff(db, "sev3_confirm_missing", 613, with_profile=False)
        service = _service(db)

        await service.save_performance(PerformanceEntrySaveSchema(oa_user_id=staff.id, month=MONTH, deduction=0))
        with pytest.raises(CustomException, match="未设置薪资档案") as exc:
            await service.confirm_performance(
                PerformanceConfirmSchema(oa_user_id=staff.id, month=MONTH, confirmed=True)
            )
        assert "sev3_confirm_missing" in str(exc.value)
        assert await _payslip_count(db) == 0
