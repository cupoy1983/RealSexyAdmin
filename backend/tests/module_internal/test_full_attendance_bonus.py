"""ADR-0011（2026-11-09 修订）全勤条件与折算基础工资不封顶的回归测试。

公司休息日当天钉钉判「正常 / 外勤」时，ADR-0009 把应出勤减 1、ADR-0010 又把这一天
加回出勤，于是 A = R + 1。用户拍板：全勤奖按 A >= R 照发，折算基础工资按
基础工资 ÷ R × A 如实计算、不封顶。

只测纯计算函数，不连数据库、不打钉钉接口。
"""

from decimal import Decimal
from types import SimpleNamespace

from app.plugin.module_internal.salary.service import SalaryService

BASE_SALARY = Decimal("5000.00")
FULL_BONUS = Decimal("200.00")
ALLOWANCE = Decimal("300.00")


def _report(required: float, actual: float) -> SimpleNamespace:
    return SimpleNamespace(
        required_days=required,
        actual_days=actual,
        missing_checkin_count=0,
        missing_checkout_count=0,
    )


def _profile() -> SimpleNamespace:
    return SimpleNamespace(
        base_salary=BASE_SALARY,
        full_attendance_bonus=FULL_BONUS,
        allowance=ALLOWANCE,
        performance_base=Decimal("0.00"),
    )


def _calc(required: float, actual: float) -> dict:
    return SalaryService._calculate(_report(required, actual), _profile(), Decimal("0"))


def test_equal_days_gets_full_award() -> None:
    """A == R：常规全勤，折算等于档案基础工资。"""
    result = _calc(22, 22)
    assert result["prorated_base"] == Decimal("5000.00")
    assert result["full_attendance_award"] == FULL_BONUS
    assert result["net_amount"] == Decimal("5500.00")


def test_worked_rest_day_keeps_full_award_without_cap() -> None:
    """A > R：公司休息日仍上班，全勤奖照发，折算基础工资允许超过档案月薪。"""
    result = _calc(21, 22)
    assert result["required_days"] == 21.0
    assert result["actual_days"] == 22.0
    # 5000 × 22 / 21 = 5238.095... → 5238.10，不裁剪到 5000
    assert result["prorated_base"] == Decimal("5238.10")
    assert result["prorated_base"] > BASE_SALARY
    assert result["full_attendance_award"] == FULL_BONUS
    assert result["net_amount"] == Decimal("5738.10")


def test_below_required_gets_no_award() -> None:
    """A < R（含半天）：不算全勤，按出勤比例折算。"""
    result = _calc(22, 21.5)
    assert result["prorated_base"] == Decimal("4886.36")
    assert result["full_attendance_award"] == Decimal("0.00")


def test_zero_required_gets_no_award() -> None:
    """R = 0：整月无应出勤日，折算与全勤奖都为 0。"""
    result = _calc(0, 1)
    assert result["prorated_base"] == Decimal("0.00")
    assert result["full_attendance_award"] == Decimal("0.00")
