"""ADR-0016 / ADR-0017 回归测试：离职结算单出单判据、读路径隔离与服务端业务日历。

不连数据库、不打钉钉接口：用顺序喂结果的 FakeSession 驱动 service，
覆盖 ADR-0016 第八节验证清单里不依赖真实库的部分。
"""

import asyncio
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.exceptions import CustomException
from app.plugin.module_internal.salary import service as salary_service
from app.plugin.module_internal.salary.schema import DismissalDeductionSchema
from app.plugin.module_internal.salary.service import DISMISSAL_SOURCE, SalaryService

CUR = date.today().strftime("%Y-%m")
PREV = SalaryService.previous_month()
USER_ID = 1
PERFORMANCE_BASE = Decimal("1000.00")


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._rows[0] if self._rows else 0


class _FakeSession:
    """按 _dismissal_plan 的固定 execute 顺序喂结果。"""

    def __init__(self, results):
        self.results = list(results)
        self.statements = []
        self.added = []

    async def execute(self, stmt):
        if not self.results:
            raise AssertionError(f"意外的第 {len(self.statements) + 1} 次查询: {stmt}")
        self.statements.append(stmt)
        return _FakeResult(self.results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None


def _auth():
    return SimpleNamespace(user=SimpleNamespace(id=1, is_superuser=True), permissions=[])


def _report(month, *, required=21, actual=21):
    return SimpleNamespace(
        oa_user_id=USER_ID,
        month=month,
        name="张三",
        dept_name="仓库",
        group_id="__test_group__",
        group_name="测试考勤组",
        required_days=required,
        actual_days=actual,
        missing_checkin_count=0,
        missing_checkout_count=0,
    )


def _profile(performance_base=PERFORMANCE_BASE):
    return SimpleNamespace(
        oa_user_id=USER_ID,
        base_salary=Decimal("5000.00"),
        full_attendance_bonus=Decimal("200.00"),
        allowance=Decimal("300.00"),
        performance_base=performance_base,
    )


def _plan_results(*, existing, reports, profile=None):
    """_dismissal_plan 的查询顺序：用户 → 档案 → 已有工资条 → 月报 → 每月(休息日 + 日明细)。"""
    return [
        [SimpleNamespace(id=USER_ID, name="张三", is_deleted=False, status=0)],
        [profile or _profile()],
        list(existing),
        list(reports),
        [],
        [],
        [],
        [],
    ]


def _service(monkeypatch, results):
    # 休息日方案配置属部署环境，测试里固定成「没有公司休息日」，补贴恒为 0。
    monkeypatch.setattr(salary_service, "load_policy", lambda: {})
    db = _FakeSession(results)
    return SalaryService(_auth(), db, redis=object()), db


# ── ADR-0017：服务端业务日历（一月跨年）─────────────────────────
def test_previous_month_crosses_year() -> None:
    assert SalaryService.previous_month(date(2027, 1, 5)) == "2026-12"
    assert SalaryService.previous_month(date(2026, 3, 1)) == "2026-02"
    calendar = SalaryService.business_calendar()
    assert calendar.previous_month == SalaryService.previous_month(date.today())
    assert calendar.current_month == CUR
    assert calendar.performance_open == (date.today().day >= 3)


# ── ADR-0016 第二节：结算单不能锁整月、不能被确认 ────────────────
def test_month_generated_and_payslip_conditions_exclude_dismissal(monkeypatch) -> None:
    service, db = _service(monkeypatch, [[0]])
    assert asyncio.run(service.month_generated(CUR)) is False
    assert "generation_source" in str(db.statements[0])
    conditions = service._payslip_conditions()
    assert any("generation_source" in str(condition) for condition in conditions)


def test_confirm_payslip_rejects_dismissal(monkeypatch) -> None:
    row = SimpleNamespace(
        id=9,
        generation_source=DISMISSAL_SOURCE,
        status=1,
        oa_user_id=USER_ID,
        month=CUR,
        user_name="张三",
    )
    service, _ = _service(monkeypatch, [[row]])
    with pytest.raises(CustomException, match="离职结算单"):
        asyncio.run(service.confirm_payslip(id=9))
    # 结算单已是「已确定」，仍不能被重复确认或改状态，也不会发「工资已发放」通知
    assert row.status == 1


# ── ADR-0016 第四节：唯一判据 = 上月是否已有正式工资条 ───────────
def test_one_settlement_when_previous_payslip_exists(monkeypatch) -> None:
    service, _ = _service(
        monkeypatch,
        _plan_results(existing=[(USER_ID, PREV, "manual")], reports=[_report(PREV), _report(CUR)]),
    )
    current, previous, plans = asyncio.run(
        service._dismissal_plan(oa_user_ids=[USER_ID], deduction_map={USER_ID: Decimal("200")})
    )
    plan = plans[0]
    assert plan.previous_payslip_exists is True
    generated = [m for m in plan.months if m.will_generate]
    assert [m.month for m in generated] == [current]
    assert generated[0].deduction == Decimal("200.00")
    assert generated[0].values["performance_amount"] == Decimal("800.00")
    prev_month = next(m for m in plan.months if m.month == previous)
    assert prev_month.will_generate is False
    assert "不重复出单" in (prev_month.reason or "")


def test_two_settlements_when_no_previous_payslip(monkeypatch) -> None:
    service, _ = _service(
        monkeypatch,
        _plan_results(existing=[], reports=[_report(PREV), _report(CUR)]),
    )
    current, previous, plans = asyncio.run(
        service._dismissal_plan(oa_user_ids=[USER_ID], deduction_map={USER_ID: Decimal("200")})
    )
    generated = [m for m in plans[0].months if m.will_generate]
    assert [m.month for m in generated] == [previous, current]
    prev_month, cur_month = generated
    # 手填扣款落在上月
    assert prev_month.deduction == Decimal("200.00")
    assert prev_month.values["performance_amount"] == Decimal("800.00")
    # 离职当月绩效全部扣除清零、不给他发
    assert cur_month.deduction == PERFORMANCE_BASE
    assert cur_month.values["performance_deduction"] == PERFORMANCE_BASE
    assert cur_month.values["performance_amount"] == Decimal("0.00")


def test_missing_report_month_is_skipped_and_annotated(monkeypatch) -> None:
    service, _ = _service(
        monkeypatch,
        _plan_results(existing=[], reports=[_report(CUR)]),
    )
    _, previous, plans = asyncio.run(
        service._dismissal_plan(oa_user_ids=[USER_ID], deduction_map={})
    )
    plan = plans[0]
    prev_month = next(m for m in plan.months if m.month == previous)
    assert prev_month.will_generate is False
    assert "暂无月报" in (prev_month.reason or "")
    assert any("暂无月报" in message for message in plan.messages)
    assert any(m.will_generate for m in plan.months)  # 其余月份照出


def test_generate_writes_dismissal_source_and_no_confirmation(monkeypatch) -> None:
    service, db = _service(
        monkeypatch,
        _plan_results(existing=[], reports=[_report(PREV), _report(CUR)]),
    )
    result = asyncio.run(
        service.generate_dismissal_settlements(
            oa_user_ids=[USER_ID],
            deductions=[DismissalDeductionSchema(oa_user_id=USER_ID, deduction=200)],
        )
    )
    assert result.generated_count == 2
    assert result.skipped == []
    assert "已生成 2 张离职结算单" in result.summary()
    assert len(db.added) == 2
    assert all(obj.generation_source == DISMISSAL_SOURCE for obj in db.added)
    assert all(obj.status == 1 for obj in db.added)
    assert all(obj.confirmed_at is not None and obj.confirmed_id == 1 for obj in db.added)
    by_month = {obj.month: obj for obj in db.added}
    assert by_month[PREV].performance_deduction == Decimal("200.00")
    assert by_month[CUR].performance_deduction == PERFORMANCE_BASE
    assert by_month[CUR].performance_amount == Decimal("0.00")


def test_generate_is_idempotent_when_month_already_settled(monkeypatch) -> None:
    service, db = _service(
        monkeypatch,
        _plan_results(
            existing=[(USER_ID, PREV, DISMISSAL_SOURCE), (USER_ID, CUR, DISMISSAL_SOURCE)],
            reports=[_report(PREV), _report(CUR)],
        ),
    )
    result = asyncio.run(
        service.generate_dismissal_settlements(oa_user_ids=[USER_ID], deductions=[])
    )
    assert result.generated_count == 0
    assert db.added == []  # 4601 重试 / 重复提交不会撞 (oa_user_id, month) 唯一约束


def test_context_marks_zero_performance_without_any_preview(monkeypatch) -> None:
    # ADR-0016 12:41（A1）：上下文只查用户 + 薪资档案两次，不碰月报 / 工资条 / 金额。
    service, db = _service(
        monkeypatch,
        [
            [SimpleNamespace(id=USER_ID, name="张三", is_deleted=False, status=0)],
            [_profile(Decimal("0.00"))],
        ],
    )
    result = asyncio.run(service.dismissal_context(oa_user_ids=[USER_ID]))
    assert result.items[0].requires_performance is False
    assert result.tip == "未满半个月无绩效，未满一个月扣除半月绩效！"
    assert len(db.statements) == 2
    assert not hasattr(result.items[0], "months")
    assert not hasattr(result, "current_month")


def test_generate_reports_missing_month_in_result(monkeypatch) -> None:
    """A3：实时拉取后仍缺月报的月份写进结果摘要，供删除响应 msg 告知管理员。"""
    service, _ = _service(
        monkeypatch,
        _plan_results(existing=[], reports=[_report(CUR)]),
    )
    result = asyncio.run(
        service.generate_dismissal_settlements(oa_user_ids=[USER_ID], deductions=[])
    )
    assert result.generated_count == 1
    assert len(result.skipped) == 1
    assert PREV in result.skipped[0]
    assert "暂无月报" in result.skipped[0]
    assert "未出单" in result.summary()
