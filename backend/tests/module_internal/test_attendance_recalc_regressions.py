"""BUG-05/06/07/10：考勤重算、日明细同步、人员口径与休息日校验回归测试。"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import AuthSchema
from app.core.database import async_db_session
from app.core.exceptions import CustomException
from app.modules.system.user.model import UserModel
from app.plugin.module_internal.attendance.model import DailyReportModel, MonthlyReportModel, RestDayModel
from app.plugin.module_internal.attendance.schema import RestDayCreateSchema
from app.plugin.module_internal.attendance.service import (
    MonthlyReportService,
    _aggregate_leaves,
    _aggregate_leaves_after_rest,
)
from app.plugin.module_internal.salary.model import SalaryProfileModel
from app.plugin.module_internal.salary.notification import SalaryNotificationService

MONTH = "2026-06"


def _rows(spec: list[tuple[str, str]]) -> list[SimpleNamespace]:
    return [SimpleNamespace(work_date=date.fromisoformat(day), approve_text=text) for day, text in spec]


# ── BUG-05：跨天假单休息日扣减 ──────────────────────────────────


def test_cross_day_leave_deducts_rest_day_share() -> None:
    """3 天病假，中间一天是公司休息日：应扣 1 天，旧逻辑会仍记 3 天。"""
    text = "病假06-02 09:00到06-04 18:00 3天"
    rows = _rows([(f"2026-06-0{d}", text) for d in (2, 3, 4)])

    old = _aggregate_leaves([r.approve_text for r in rows if r.work_date != date(2026, 6, 3)])
    assert old["病假"] == 3.0, "复现原缺陷：剔除休息日文本后仍计整单全量"

    new = _aggregate_leaves_after_rest(rows, month=MONTH, rest_dates={date(2026, 6, 3)})
    assert new["病假"] == 2.0


def test_half_day_leave_on_rest_day_is_fully_deducted() -> None:
    text = "病假06-02 13:00到06-02 17:30 0.5天"
    rows = _rows([("2026-06-02", text)])
    result = _aggregate_leaves_after_rest(rows, month=MONTH, rest_dates={date(2026, 6, 2)})
    assert result["病假"] == 0.0


def test_cross_day_leave_with_half_day_boundary() -> None:
    """下午起请到第三天全天（0.5 + 1 + 1 = 2.5 天），休息日落首日应扣 0.5。"""
    text = "事假06-02 13:00到06-04 18:00 2.5天"
    rows = _rows([(f"2026-06-0{d}", text) for d in (2, 3, 4)])
    result = _aggregate_leaves_after_rest(rows, month=MONTH, rest_dates={date(2026, 6, 2)})
    assert result["事假"] == 2.0


def test_no_rest_day_matches_original_aggregation() -> None:
    """无休息日时新算法与原聚合完全一致，保证重算幂等。"""
    first = "年假06-05 09:00到06-05 18:00 1天"
    second = "病假06-10 13:00到06-10 17:30 0.5天"
    rows = _rows([("2026-06-05", first), ("2026-06-10", second)])
    old = _aggregate_leaves([r.approve_text for r in rows])
    new = _aggregate_leaves_after_rest(rows, month=MONTH, rest_dates=set())
    assert new == old


def test_unparsable_text_falls_back_to_average_split() -> None:
    """文本无起止时间时按出现天数平均拆分，不能把整单量全算到剩余工作日。"""
    text = "事假 2天"
    rows = _rows([("2026-06-11", text), ("2026-06-12", text)])
    result = _aggregate_leaves_after_rest(rows, month=MONTH, rest_dates={date(2026, 6, 11)})
    assert result["事假"] == 1.0


# ── BUG-06：同步时清理已清空的日明细 ────────────────────────────


async def _seed_user(db: AsyncSession, username: str) -> UserModel:
    user = UserModel(username=username, name=username, password="x", status=0, is_deleted=False)
    db.add(user)
    await db.flush()
    return user


async def test_sync_removes_stale_daily_rows_only_for_target(test_client: TestClient) -> None:
    async with async_db_session() as db:
        target = await _seed_user(db, "bug06_target")
        other = await _seed_user(db, "bug06_other")
        stale = DailyReportModel(
            oa_user_id=target.id,
            work_date=date(2026, 6, 2),
            month=MONTH,
            day_of_month=2,
            group_id="G-BUG06",
            group_name="缺陷测试组",
            raw_text="迟到",
            approve_text="事假06-02 09:00到06-02 18:00 1天",
            effective_text="迟到",
            status=0,
            is_deleted=False,
        )
        keep = DailyReportModel(
            oa_user_id=other.id,
            work_date=date(2026, 6, 2),
            month=MONTH,
            day_of_month=2,
            group_id="G-BUG06",
            group_name="缺陷测试组",
            raw_text="正常",
            status=0,
            is_deleted=False,
        )
        db.add_all([stale, keep])
        await db.flush()

        service = MonthlyReportService(AuthSchema(), db)
        await service._save_daily_rows(
            oa_user_id=target.id,
            month=MONTH,
            start=date(2026, 6, 1),
            end=date(2026, 6, 30),
            day_result_id="R",
            day_approve_id="A",
            group_id="G-BUG06",
            group_name="缺陷测试组",
            values_by_column={"R": {}, "A": {}},
        )

        remaining = (
            await db.execute(select(DailyReportModel).where(DailyReportModel.month == MONTH))
        ).scalars().all()
        assert [r.oa_user_id for r in remaining] == [other.id], "清空日期的旧行应删除，且不得误删其他用户"


# ── BUG-07：待设置绩效名单排除停用与软删员工 ────────────────────


async def test_pending_setting_excludes_disabled_and_deleted_users(test_client: TestClient) -> None:
    async with async_db_session() as db:
        users: dict[str, int] = {}
        for label, status, deleted in (("active", 0, False), ("disabled", 1, False), ("deleted", 0, True)):
            user = UserModel(
                username=f"bug07_{label}",
                name=label,
                password="x",
                status=status,
                is_deleted=deleted,
            )
            db.add(user)
            await db.flush()
            users[label] = user.id
            db.add(
                MonthlyReportModel(
                    oa_user_id=user.id,
                    month=MONTH,
                    group_id="G-BUG07",
                    status=0,
                    is_deleted=False,
                )
            )
            db.add(
                SalaryProfileModel(
                    oa_user_id=user.id,
                    base_salary=5000,
                    performance_base=1000,
                    status=0,
                    is_deleted=False,
                )
            )
        await db.flush()

        pending = await SalaryNotificationService(db, AsyncMock()).pending_setting_user_ids(MONTH)
        assert users["active"] in pending
        assert users["disabled"] not in pending
        assert users["deleted"] not in pending


# ── BUG-10：考勤组失效时拒绝写入休息日 ──────────────────────────


async def test_create_rest_days_rejects_unknown_group(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """方案已配置、但绑定表查不到组名时，仍按 BUG-10 拒绝写入。"""
    monkeypatch.setenv("INTERNAL_REST_DAY_POLICY", '{"G-BUG10-NOT-EXIST":{"mode":1}}')
    async with async_db_session() as db:
        service = MonthlyReportService(AuthSchema(), db)
        with pytest.raises(CustomException, match="考勤组不存在或已失效"):
            await service.create_rest_days(
                RestDayCreateSchema(group_id="G-BUG10-NOT-EXIST", dates=[date(2026, 6, 15)])
            )
        count = (
            await db.execute(select(func.count(RestDayModel.id)).where(RestDayModel.group_id == "G-BUG10-NOT-EXIST"))
        ).scalar()
        assert count == 0


# ── 复核 A1 甲：按（员工, 月份）去重 + 列缺失不误删 ──────────────

DEDUP_MONTH = "2031-08"


async def test_sync_month_skips_already_synced_users(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一个人同一个月默认只拉一次；只有 force=True 才允许显式重拉。"""
    from app.plugin.module_internal.account.model import ExternalAccountBindingModel
    from app.plugin.module_internal.account.schema import SYSTEM_DINGTALK

    async with async_db_session() as db:
        done = await _seed_user(db, "dedup_done")
        fresh = await _seed_user(db, "dedup_fresh")
        for user, ding in ((done, "dedup_ding_done"), (fresh, "dedup_ding_fresh")):
            db.add(
                ExternalAccountBindingModel(
                    system_type=SYSTEM_DINGTALK,
                    oa_user_id=user.id,
                    external_user_id=ding,
                    mobile=f"137{user.id:08d}"[-11:],
                    group_id="G-DEDUP",
                    group_name="去重测试组",
                    status=0,
                    is_deleted=False,
                )
            )
        db.add(
            MonthlyReportModel(
                oa_user_id=done.id, month=DEDUP_MONTH, group_id="G-DEDUP", status=0, is_deleted=False
            )
        )
        await db.flush()

        client = AsyncMock()
        client.get_attendance_columns.return_value = []
        client.get_user_group.return_value = {"group_id": "G-DEDUP", "name": "去重测试组"}
        client.get_attendance_column_values.return_value = []
        client.get_user_detail.return_value = {}
        monkeypatch.setattr(MonthlyReportService, "_require_configured", lambda self: None)
        monkeypatch.setattr(MonthlyReportService, "_client", lambda self: client)

        service = MonthlyReportService(AuthSchema(), db)

        result = await service.sync_month(month=DEDUP_MONTH)
        assert result.total == 1, "已有月报的人应被跳过，只补缺的人"
        assert any("去重跳过" in m for m in result.messages)
        assert client.get_attendance_column_values.await_count > 0

        client.get_attendance_column_values.reset_mock()
        again = await service.sync_month(month=DEDUP_MONTH)
        assert again.total == 0, "第二次调用全员已同步，不应再拉任何人"
        assert client.get_attendance_column_values.await_count == 0, "去重后不应再打钉钉取数接口"

        client.get_attendance_column_values.reset_mock()
        forced = await service.sync_month(month=DEDUP_MONTH, force=True)
        assert forced.total == 2, "force=True 必须跳过按人去重，允许显式强制刷新"
        assert client.get_attendance_column_values.await_count > 0


async def test_missing_column_keys_keep_existing_daily_rows(test_client: TestClient) -> None:
    """钉钉本次没返回两列 key 时，不得把整月日明细当成「已清空」删光。"""
    async with async_db_session() as db:
        user = await _seed_user(db, "dedup_missing_cols")
        db.add(
            DailyReportModel(
                oa_user_id=user.id,
                work_date=date(2031, 8, 3),
                month=DEDUP_MONTH,
                day_of_month=3,
                group_id="G-DEDUP",
                group_name="去重测试组",
                raw_text="下班缺卡",
                effective_text="下班缺卡",
                status=0,
                is_deleted=False,
            )
        )
        await db.flush()

        service = MonthlyReportService(AuthSchema(), db)
        kwargs = {
            "oa_user_id": user.id,
            "month": DEDUP_MONTH,
            "start": date(2031, 8, 1),
            "end": date(2031, 8, 31),
            "day_result_id": "R",
            "day_approve_id": "A",
            "group_id": "G-DEDUP",
            "group_name": "去重测试组",
        }

        await service._save_daily_rows(**kwargs, values_by_column={})
        remaining = (
            await db.execute(
                select(DailyReportModel).where(
                    DailyReportModel.month == DEDUP_MONTH,
                    DailyReportModel.oa_user_id == user.id,
                )
            )
        ).scalars().all()
        assert len(remaining) == 1, "列缺失必须保留旧行（漏删可自愈，误删不可逆）"
        assert remaining[0].raw_text == "下班缺卡"

        # 列齐全且钉钉确实清空时，仍按 BUG-06 正常清理
        await service._save_daily_rows(**kwargs, values_by_column={"R": {}, "A": {}})
        left = (
            await db.execute(
                select(DailyReportModel).where(
                    DailyReportModel.month == DEDUP_MONTH,
                    DailyReportModel.oa_user_id == user.id,
                )
            )
        ).scalars().all()
        assert left == [], "列齐全且钉钉确实清空时仍要正常删除旧行"
