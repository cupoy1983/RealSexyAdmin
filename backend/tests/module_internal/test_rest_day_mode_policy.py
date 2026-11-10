"""公司休息日「考勤组方案」（env mode=1/2）回归测试。

「休息」方案：沿用 ADR-0009 现行重算。
「补工资」方案：考勤统计完全不动，工资条按当天钉钉原文逐天判定是否发补贴。
"""

import json
from datetime import date
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
from app.plugin.module_internal.attendance.model import DailyReportModel, MonthlyReportModel, RestDayModel
from app.plugin.module_internal.attendance.schema import RestDayCreateSchema
from app.plugin.module_internal.attendance.service import MonthlyReportService
from app.plugin.module_internal.config import NO_POLICY_MESSAGE, get_policy, load_policy
from app.plugin.module_internal.salary.model import PayslipModel, SalaryProfileModel
from app.plugin.module_internal.salary.service import SalaryService

MONTH = "2032-05"
OFFICE = "<见本机运维记录>"
WAREHOUSE = "330445194"
UNCONFIGURED = "999999999"
D1, D2, D3 = date(2032, 5, 10), date(2032, 5, 11), date(2032, 5, 12)
LEAVE_TEXT = "病假05-10 09:00到05-11 18:00 2天"


def _env(*, office: bool = True, warehouse: bool = True, money: int = 100) -> str:
    cfg: dict[str, dict] = {}
    if office:
        cfg[OFFICE] = {"mode": 1}
    if warehouse:
        cfg[WAREHOUSE] = {"mode": 2, "money": money}
    return json.dumps(cfg, ensure_ascii=False)


async def _month_synced(self, month: str) -> bool:
    """补工资方案用例只测工资条计算，考勤同步状态不在其覆盖范围，固定视为已同步。

    生成硬闸要求「考勤已拉全 + 全员有档案」，这里 mock 掉第一项；否则没有
    钉钉绑定的测试员工会被判成未同步（ADR-0012 严重 3 修复后的适配）。
    """
    return True


async def _always_confirmed(self, month: str) -> bool:
    """ADR-0018 新增的考勤确认闸：本文件只测休息日方案 / 工资条计算，
    固定视为「该月考勤已确认」，确认闸本身由 test_attendance_month_confirm.py 覆盖。
    """
    return True


async def _seed_user(db: AsyncSession, username: str) -> UserModel:
    user = UserModel(username=username, name=username, password="x", status=0, is_deleted=False)
    db.add(user)
    await db.flush()
    return user


def _base_stats() -> dict:
    return {
        "required_days": 22,
        "actual_days": 22,
        "rest_days": 0,
        "late_count": 1,
        "serious_late_count": 0,
        "absent_late_days": 0,
        "early_leave_count": 0,
        "missing_checkin_count": 1,
        "missing_checkout_count": 0,
        "absent_days": 0,
        "leave_personal": 0,
        "leave_sick": 0,
        "leave_annual": 0,
    }


async def _seed_binding(db: AsyncSession, user: UserModel, group_id: str, group_name: str, seq: int) -> None:
    db.add(
        ExternalAccountBindingModel(
            system_type=SYSTEM_DINGTALK,
            oa_user_id=user.id,
            external_user_id=f"policy_ding_{seq}",
            mobile=f"138{seq:08d}"[:11],
            group_id=group_id,
            group_name=group_name,
            status=0,
            is_deleted=False,
        )
    )


async def _seed_report(db: AsyncSession, user: UserModel, group_id: str, group_name: str) -> MonthlyReportModel:
    stats = _base_stats()
    report = MonthlyReportModel(
        oa_user_id=user.id,
        month=MONTH,
        group_id=group_id,
        group_name=group_name,
        name=user.name,
        status=0,
        is_deleted=False,
        dingtalk_raw=dict(stats),
        **stats,
    )
    db.add(report)
    return report


async def _seed_daily(
    db: AsyncSession,
    user: UserModel,
    group_id: str,
    group_name: str,
    specs: list[tuple[date, str, str | None]],
) -> None:
    for day, raw, approve in specs:
        db.add(
            DailyReportModel(
                oa_user_id=user.id,
                work_date=day,
                month=MONTH,
                day_of_month=day.day,
                group_id=group_id,
                group_name=group_name,
                raw_text=raw,
                approve_text=approve,
                effective_text=raw,
                status=0,
                is_deleted=False,
            )
        )


async def _seed_rest(db: AsyncSession, group_id: str, group_name: str, days: list[date]) -> None:
    for day in days:
        db.add(
            RestDayModel(
                group_id=group_id,
                group_name=group_name,
                rest_date=day,
                month=MONTH,
                status=0,
                is_deleted=False,
            )
        )


# ── 配置读取与校验 ────────────────────────────────────────────


def test_policy_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """mode=1 不带 money，mode=2 带 money；未配置的组返回 None。"""
    monkeypatch.setenv("INTERNAL_REST_DAY_POLICY", _env())
    policy = load_policy()
    assert policy[OFFICE].mode == 1 and policy[OFFICE].money is None
    assert policy[WAREHOUSE].mode == 2 and policy[WAREHOUSE].money == Decimal("100")
    assert get_policy(OFFICE).mode == 1  # type: ignore[union-attr]
    assert get_policy(UNCONFIGURED) is None


@pytest.mark.parametrize(
    "raw",
    [
        '{"1":{"mode":3}}',  # 未知 mode 必须报错
        '{"1":{"mode":2}}',  # 补工资方案缺 money
        '{"1":{"mode":2,"money":0}}',  # money 必须为正
        '{"1":{"mode":1,"money":50}}',  # 休息方案不需要 money
        "not-json",
    ],
)
def test_invalid_policy_rejected(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("INTERNAL_REST_DAY_POLICY", raw)
    with pytest.raises(CustomException):
        load_policy()


# ── 登记公司休息日 ────────────────────────────────────────────


async def test_create_rest_days_requires_policy(test_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """没配 group_id 一律拒绝，提示固定文案，且不写库。"""
    monkeypatch.setenv("INTERNAL_REST_DAY_POLICY", "{}")
    async with async_db_session() as db:
        user = await _seed_user(db, "policy_no_config")
        await _seed_binding(db, user, WAREHOUSE, "DY-仓库", 601)
        await db.flush()

        with pytest.raises(CustomException, match=NO_POLICY_MESSAGE):
            await MonthlyReportService(AuthSchema(), db).create_rest_days(
                RestDayCreateSchema(group_id=WAREHOUSE, dates=[D1])
            )
        rows = (
            await db.execute(select(RestDayModel).where(RestDayModel.group_id == WAREHOUSE))
        ).scalars().all()
        assert rows == []


# ── 考勤重算按 mode 分叉 ─────────────────────────────────────


async def test_mode1_keeps_legacy_recalc(test_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """休息方案：应出勤减 1、日明细显示「公司休息」、请假按休息日占比扣减。"""
    monkeypatch.setenv("INTERNAL_REST_DAY_POLICY", _env())
    async with async_db_session() as db:
        user = await _seed_user(db, "policy_office")
        await _seed_report(db, user, OFFICE, "DY-OFFICE")
        await _seed_daily(
            db, user, OFFICE, "DY-OFFICE",
            [(D1, "正常", LEAVE_TEXT), (D2, "正常", LEAVE_TEXT)],
        )
        await _seed_rest(db, OFFICE, "DY-OFFICE", [D1])
        await db.flush()

        await MonthlyReportService(AuthSchema(), db).recalc_month(MONTH)
        report = (
            await db.execute(select(MonthlyReportModel).where(MonthlyReportModel.oa_user_id == user.id))
        ).scalars().one()
        daily = (
            await db.execute(
                select(DailyReportModel).where(
                    DailyReportModel.oa_user_id == user.id, DailyReportModel.work_date == D1
                )
            )
        ).scalars().one()
        assert report.required_days == 21, "休息方案仍要减应出勤"
        assert report.leave_sick == 1, "跨天假单要扣掉落在公司休息日的那 1 天"
        assert daily.effective_text == "公司休息"


async def test_mode2_does_not_touch_statistics(test_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """补工资方案：不减应出勤、不清异常、不扣请假占比、日明细保留钉钉原文。"""
    monkeypatch.setenv("INTERNAL_REST_DAY_POLICY", _env())
    async with async_db_session() as db:
        user = await _seed_user(db, "policy_warehouse")
        await _seed_report(db, user, WAREHOUSE, "DY-仓库")
        await _seed_daily(
            db, user, WAREHOUSE, "DY-仓库",
            [(D1, "迟到", LEAVE_TEXT), (D2, "正常", LEAVE_TEXT)],
        )
        await _seed_rest(db, WAREHOUSE, "DY-仓库", [D1])
        await db.flush()

        await MonthlyReportService(AuthSchema(), db).recalc_month(MONTH)
        report = (
            await db.execute(select(MonthlyReportModel).where(MonthlyReportModel.oa_user_id == user.id))
        ).scalars().one()
        daily = (
            await db.execute(
                select(DailyReportModel).where(
                    DailyReportModel.oa_user_id == user.id, DailyReportModel.work_date == D1
                )
            )
        ).scalars().one()
        assert report.required_days == 22, "补工资方案不扣应出勤"
        assert report.late_count == 1, "补工资方案不清异常计数"
        assert report.leave_sick == 2, "补工资方案不按休息日占比扣请假"
        assert report.actual_days == 20, "请假照算，出勤按 22 - 2 少两天"
        assert daily.effective_text == "迟到", "日明细保留钉钉原文"


# ── 工资条补贴按当天出勤逐天判定 ───────────────────────────────


async def test_generate_month_pays_only_worked_rest_days(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """正常 / 缺卡发补贴，请假不发；未配置的组按「没有公司休息日」算，不报警。"""
    monkeypatch.setenv("INTERNAL_REST_DAY_POLICY", _env(office=False))
    monkeypatch.setattr(MonthlyReportService, "month_synced", _month_synced)
    monkeypatch.setattr(MonthlyReportService, "month_confirmed", _always_confirmed)
    async with async_db_session() as db:
        warehouse_user = await _seed_user(db, "policy_payslip_wh")
        plain_user = await _seed_user(db, "policy_payslip_plain")
        await _seed_report(db, warehouse_user, WAREHOUSE, "DY-仓库")
        await _seed_report(db, plain_user, UNCONFIGURED, "未配置组")
        await _seed_daily(
            db, warehouse_user, WAREHOUSE, "DY-仓库",
            [(D1, "正常", None), (D2, "病假", LEAVE_TEXT), (D3, "上班缺卡", None)],
        )
        await _seed_daily(db, plain_user, UNCONFIGURED, "未配置组", [(D1, "正常", None)])
        await _seed_rest(db, WAREHOUSE, "DY-仓库", [D1, D2, D3])
        await _seed_rest(db, UNCONFIGURED, "未配置组", [D1])
        for user in (warehouse_user, plain_user):
            db.add(
                SalaryProfileModel(
                    oa_user_id=user.id,
                    base_salary=5000,
                    full_attendance_bonus=0,
                    allowance=300,
                    performance_base=0,
                    status=0,
                    is_deleted=False,
                )
            )
        await db.flush()

        service = SalaryService(AuthSchema(user=CoreUserSchema(id=990501, is_superuser=True)), db)
        result = await service.generate_month(month=MONTH, source="manual")
        assert result.generated == 2

        payslips = {
            p.oa_user_id: p
            for p in (await db.execute(select(PayslipModel).where(PayslipModel.month == MONTH))).scalars().all()
        }
        warehouse = payslips[warehouse_user.id]
        assert warehouse.allowance == Decimal("500"), "档案 300 + 正常/缺卡两天 × 100"
        assert warehouse.missing_deduction == Decimal("10"), "缺卡仍按 10*n² 扣款"
        plain = payslips[plain_user.id]
        assert plain.allowance == Decimal("300"), "未配置组按没有公司休息日算"


async def test_unrecognized_dingtalk_text_blocks_generation(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """钉钉文案改了、程序不认识时必须报错，不能静默按「不发补贴」算过去。"""
    monkeypatch.setenv("INTERNAL_REST_DAY_POLICY", _env(office=False))
    monkeypatch.setattr(MonthlyReportService, "month_synced", _month_synced)
    monkeypatch.setattr(MonthlyReportService, "month_confirmed", _always_confirmed)
    async with async_db_session() as db:
        user = await _seed_user(db, "policy_unknown_text")
        await _seed_report(db, user, WAREHOUSE, "DY-仓库")
        await _seed_daily(db, user, WAREHOUSE, "DY-仓库", [(D1, "全新异常类型", None)])
        await _seed_rest(db, WAREHOUSE, "DY-仓库", [D1])
        db.add(
            SalaryProfileModel(
                oa_user_id=user.id,
                base_salary=5000,
                full_attendance_bonus=0,
                allowance=300,
                performance_base=0,
                status=0,
                is_deleted=False,
            )
        )
        await db.flush()

        service = SalaryService(AuthSchema(user=CoreUserSchema(id=990502, is_superuser=True)), db)
        with pytest.raises(CustomException, match="钉钉考勤结果文案无法识别"):
            await service.generate_month(month=MONTH, source="manual")
        count = (
            await db.execute(select(func.count(PayslipModel.id)).where(PayslipModel.month == MONTH))
        ).scalar()
        assert count == 0, "整批原子：报错时不得留下任何工资条"


async def test_missing_daily_row_does_not_block_generation(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """文本为空 / 没有日明细行是正常业务（如月中入职），不报错，只是不发补贴。"""
    monkeypatch.setenv("INTERNAL_REST_DAY_POLICY", _env(office=False))
    monkeypatch.setattr(MonthlyReportService, "month_synced", _month_synced)
    monkeypatch.setattr(MonthlyReportService, "month_confirmed", _always_confirmed)
    async with async_db_session() as db:
        user = await _seed_user(db, "policy_no_daily_row")
        await _seed_report(db, user, WAREHOUSE, "DY-仓库")
        await _seed_rest(db, WAREHOUSE, "DY-仓库", [D1])
        db.add(
            SalaryProfileModel(
                oa_user_id=user.id,
                base_salary=5000,
                full_attendance_bonus=0,
                allowance=300,
                performance_base=0,
                status=0,
                is_deleted=False,
            )
        )
        await db.flush()

        service = SalaryService(AuthSchema(user=CoreUserSchema(id=990503, is_superuser=True)), db)
        result = await service.generate_month(month=MONTH, source="manual")
        assert result.generated == 1
        payslip = (
            await db.execute(select(PayslipModel).where(PayslipModel.month == MONTH))
        ).scalars().one()
        assert payslip.allowance == Decimal("300")
