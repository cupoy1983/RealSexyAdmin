"""ADR-0018：考勤确认与工资条生成锁定回归用例。

覆盖复核报告功能缺陷 3：
- 未确认不生成整月工资条（两条自动生成路径共用的第三道闸：最后一人绩效确认、
  每日任务全员零绩效；`generate_month()` 没有独立手动接口）；
- 确认后 / 已生成工资条后，公司休息日不可再改；
- 撤销确认可重新开放休息日，已生成工资条的月份连撤销也拒绝；
- 离职结算单不参与锁定，不受考勤确认影响；
- sync-state 的三个确认状态字段语义正确。

本文件不 mock `month_confirmed()` / `_has_formal_payslip()`，测的就是真实闸；
只 mock `month_synced()`，因为共享测试库里其它用例的绑定数据会影响「应到人员名单」。
"""

import json
from datetime import UTC, date, datetime
from decimal import Decimal

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
from app.plugin.module_internal.attendance.model import (
    MonthConfirmationModel,
    MonthlyReportModel,
    RestDayModel,
)
from app.plugin.module_internal.attendance.schema import MonthConfirmSchema, RestDayCreateSchema
from app.plugin.module_internal.attendance.service import MonthlyReportService
from app.plugin.module_internal.salary.model import PayslipModel, SalaryProfileModel
from app.plugin.module_internal.salary.service import SalaryService

GROUP_ID = "G-ADR18"


async def _always_synced(self, month: str) -> bool:
    return True


async def _never_synced(self, month: str) -> bool:
    return False


def _auth() -> AuthSchema:
    return AuthSchema(user=CoreUserSchema(id=990801, is_superuser=True))


def _svc(db: AsyncSession) -> MonthlyReportService:
    return MonthlyReportService(_auth(), db)


def _salary(db: AsyncSession) -> SalaryService:
    return SalaryService(_auth(), db)


async def _seed(db: AsyncSession, *, username: str, seq: int, month: str) -> UserModel:
    """一名有钉钉绑定、有当月月报、有薪资档案的在职员工。"""
    user = UserModel(username=username, name=username, password="x", status=0, is_deleted=False)
    db.add(user)
    await db.flush()
    db.add(
        ExternalAccountBindingModel(
            system_type=SYSTEM_DINGTALK,
            oa_user_id=user.id,
            external_user_id=f"adr18_ding_{seq}",
            mobile=f"139{seq:08d}"[:11],
            group_id=GROUP_ID,
            group_name="确认闸测试组",
            status=0,
            is_deleted=False,
        )
    )
    db.add(
        MonthlyReportModel(
            oa_user_id=user.id,
            month=month,
            group_id=GROUP_ID,
            group_name="确认闸测试组",
            name=username,
            required_days=21,
            actual_days=21,
            status=0,
            is_deleted=False,
        )
    )
    db.add(
        SalaryProfileModel(
            oa_user_id=user.id,
            base_salary=Decimal("5000"),
            full_attendance_bonus=Decimal("200"),
            allowance=Decimal("0"),
            performance_base=Decimal("0"),
            status=0,
            is_deleted=False,
        )
    )
    await db.flush()
    return user


def _payslip(user: UserModel, month: str, source: str) -> PayslipModel:
    return PayslipModel(
        oa_user_id=user.id,
        month=month,
        generation_source=source,
        generated_at=datetime.now(UTC),
        status=1,
        is_deleted=False,
    )


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_REST_DAY_POLICY", json.dumps({GROUP_ID: {"mode": 1}}))
    monkeypatch.setattr(MonthlyReportService, "month_synced", _always_synced)


MONTH = "2050-07"


async def test_unconfirmed_month_blocks_generation(test_client: TestClient) -> None:
    """未确认一律整月不生成，且错误信息指明要到考勤统计页确认。"""
    async with async_db_session() as db:
        await _seed(db, username="adr18_01", seq=701, month=MONTH)
        blockers = await _salary(db).generation_blockers(MONTH)
        assert len(blockers) == 1
        assert "考勤尚未确认" in blockers[0]
        assert MONTH in blockers[0]


async def test_confirm_clears_generation_blocker(test_client: TestClient) -> None:
    """确认之后（档案齐全的前提下）生成闸放行。"""
    async with async_db_session() as db:
        await _seed(db, username="adr18_02", seq=702, month="2050-08")
        svc = _svc(db)
        assert await svc.month_confirmed("2050-08") is False
        await svc.confirm_month(MonthConfirmSchema(month="2050-08"))
        assert await svc.month_confirmed("2050-08") is True
        assert await _salary(db).generation_blockers("2050-08") == []


async def test_confirmed_month_blocks_rest_day_create(test_client: TestClient) -> None:
    """确认后登记休息日被整批拒绝，且不写库。"""
    month = "2050-09"
    async with async_db_session() as db:
        await _seed(db, username="adr18_03", seq=703, month=month)
        svc = _svc(db)
        await svc.confirm_month(MonthConfirmSchema(month=month))
        with pytest.raises(CustomException, match="考勤已确认"):
            await svc.create_rest_days(
                RestDayCreateSchema(group_id=GROUP_ID, dates=[date(2050, 9, 15)])
            )
        count = (
            await db.execute(select(func.count(RestDayModel.id)).where(RestDayModel.month == month))
        ).scalar()
        assert count == 0


async def test_revoke_reopens_rest_day_and_allows_reconfirm(test_client: TestClient) -> None:
    """撤销后休息日重新可编辑，且可以再次确认（upsert 幂等）。"""
    month = "2050-10"
    async with async_db_session() as db:
        await _seed(db, username="adr18_04", seq=704, month=month)
        svc = _svc(db)
        await svc.confirm_month(MonthConfirmSchema(month=month))
        await svc.revoke_month_confirmation(MonthConfirmSchema(month=month))
        assert await svc.month_confirmed(month) is False
        await svc._assert_rest_days_editable({month})
        await svc.confirm_month(MonthConfirmSchema(month=month))
        assert await svc.month_confirmed(month) is True


async def test_generated_payslip_locks_everything(test_client: TestClient) -> None:
    """已生成正式工资条：休息日、撤销确认、重复确认全被拒。"""
    month = "2050-11"
    async with async_db_session() as db:
        user = await _seed(db, username="adr18_05", seq=705, month=month)
        svc = _svc(db)
        await svc.confirm_month(MonthConfirmSchema(month=month))
        db.add(_payslip(user, month, "manual"))
        await db.flush()

        assert await svc._has_formal_payslip(month) is True
        with pytest.raises(CustomException, match="已生成正式工资条"):
            await svc._assert_rest_days_editable({month})
        with pytest.raises(CustomException, match="已生成正式工资条"):
            await svc.revoke_month_confirmation(MonthConfirmSchema(month=month))
        with pytest.raises(CustomException, match="已生成正式工资条"):
            await svc.confirm_month(MonthConfirmSchema(month=month))


async def test_dismissal_payslip_does_not_lock_attendance(test_client: TestClient) -> None:
    """离职结算单不算正式工资条：不锁休息日，也不阻止该月考勤确认。"""
    month = "2050-12"
    async with async_db_session() as db:
        user = await _seed(db, username="adr18_06", seq=706, month=month)
        svc = _svc(db)
        db.add(_payslip(user, month, "dismissal"))
        await db.flush()

        assert await svc._has_formal_payslip(month) is False
        await svc._assert_rest_days_editable({month})
        await svc.confirm_month(MonthConfirmSchema(month=month))
        assert await svc.month_confirmed(month) is True


async def test_delete_rest_days_checks_month_before_delete(test_client: TestClient) -> None:
    """删除入口按被删记录所属月份硬拦，硬拦时记录仍在。"""
    month = "2051-01"
    async with async_db_session() as db:
        await _seed(db, username="adr18_07", seq=707, month=month)
        svc = _svc(db)
        await svc.confirm_month(MonthConfirmSchema(month=month))
        rest = RestDayModel(
            group_id=GROUP_ID,
            group_name="确认闸测试组",
            rest_date=date(2051, 1, 15),
            month=month,
            status=0,
            is_deleted=False,
        )
        db.add(rest)
        await db.flush()
        rest_id = rest.id

        with pytest.raises(CustomException, match="考勤已确认"):
            await svc.delete_rest_days([rest_id])
        remaining = (
            await db.execute(select(func.count(RestDayModel.id)).where(RestDayModel.id == rest_id))
        ).scalar()
        assert remaining == 1


async def test_sync_state_reports_confirmation_flags(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sync-state 的三个新字段随确认 / 出单状态变化，无需额外请求。"""
    month = "2051-02"
    monkeypatch.setattr(
        MonthlyReportService, "_previous_month", classmethod(lambda cls, today=None: month)
    )
    async with async_db_session() as db:
        user = await _seed(db, username="adr18_08", seq=708, month=month)
        svc = _svc(db)

        state = await svc.previous_month_sync_state()
        assert state.month == month
        assert state.confirmed is False and state.can_confirm is True and state.rest_editable is True

        await svc.confirm_month(MonthConfirmSchema(month=month))
        state = await svc.previous_month_sync_state()
        assert state.confirmed is True and state.can_confirm is True and state.rest_editable is False

        db.add(_payslip(user, month, "manual"))
        await db.flush()
        state = await svc.previous_month_sync_state()
        assert state.confirmed is True and state.can_confirm is False and state.rest_editable is False


async def test_confirm_requires_full_sync(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """考勤没拉全不能确认，避免确认一份不完整的数据。"""
    month = "2051-03"
    monkeypatch.setattr(MonthlyReportService, "month_synced", _never_synced)
    async with async_db_session() as db:
        await _seed(db, username="adr18_09", seq=709, month=month)
        svc = _svc(db)
        with pytest.raises(CustomException, match="尚未全量同步"):
            await svc.confirm_month(MonthConfirmSchema(month=month))
        assert await svc.month_confirmed(month) is False


async def test_confirmed_month_cannot_be_resynced(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """已确认月份即使因人员变动变成「未全量同步」，手动补拉与定时任务也必须跳过。

    这是 ADR-0018 第三节的边界：只靠 can_sync（已同步即拒绝）会被绕过。
    """
    month = "2051-05"
    monkeypatch.setattr(
        MonthlyReportService, "_previous_month", classmethod(lambda cls, today=None: month)
    )
    async with async_db_session() as db:
        await _seed(db, username="adr18_11", seq=711, month=month)
        svc = _svc(db)
        await svc.confirm_month(MonthConfirmSchema(month=month))

        # 模拟新人进考勤组、该月被重新判成「未全量同步」
        monkeypatch.setattr(MonthlyReportService, "month_synced", _never_synced)
        state = await svc.previous_month_sync_state()
        assert state.can_sync is False, "已确认月份不能再显示手动补拉按钮"

        result = await svc.sync_previous_month()
        assert result.month == month
        assert result.success == 0
        assert any("考勤已确认" in m for m in result.messages)


async def test_confirm_revives_soft_deleted_row(test_client: TestClient) -> None:
    """历史软删行不会卡住 month 唯一约束：再确认走 upsert 复活且只剩一行。"""
    month = "2051-04"
    async with async_db_session() as db:
        await _seed(db, username="adr18_10", seq=710, month=month)
        svc = _svc(db)
        await svc.confirm_month(MonthConfirmSchema(month=month))
        row = (
            await db.execute(
                select(MonthConfirmationModel).where(MonthConfirmationModel.month == month)
            )
        ).scalars().first()
        assert row is not None
        row.is_deleted = True
        await db.flush()
        assert await svc.month_confirmed(month) is False

        await svc.confirm_month(MonthConfirmSchema(month=month))
        assert await svc.month_confirmed(month) is True
        total = (
            await db.execute(
                select(func.count(MonthConfirmationModel.id)).where(
                    MonthConfirmationModel.month == month
                )
            )
        ).scalar()
        assert total == 1
