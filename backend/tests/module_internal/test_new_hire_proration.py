"""月中入职当月工资折算回归测试（ADR-0009 A4 / ADR-0010 / ADR-0011 第十一节）。

缺陷（2026-11-11 杜总报）：钉钉按人按天返回应出勤列值，新人只覆盖入职后日期，
个人 R 被截成 9；ADR-0010 自算的 A 也同时被截成 9，A/R = 1，于是 4000 元基础工资
发成整月 4000。修复：`generate_month` 把低于全组标准的个人 R 抬平到同组众数（23），
**同组正常员工金额一分不变**；折算公式本身不改。

只测纯计算与分组标准，不连数据库、不打钉钉接口。
"""

from decimal import Decimal
from types import SimpleNamespace

from app.plugin.module_internal.salary.service import SalaryService

GROUP_ID = "DY-WAREHOUSE"


def _report(required: float, actual: float, *, group_id: str = GROUP_ID, oa_user_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        required_days=required,
        actual_days=actual,
        missing_checkin_count=0,
        missing_checkout_count=0,
        group_id=group_id,
        oa_user_id=oa_user_id,
        month="2026-10",
    )


def _profile(base: Decimal = Decimal("4000.00")) -> SimpleNamespace:
    return SimpleNamespace(
        base_salary=base,
        full_attendance_bonus=Decimal("0.00"),
        allowance=Decimal("0.00"),
        performance_base=Decimal("0.00"),
    )


def _calc(required: float, actual: float, *, override: float | None = None) -> dict:
    return SalaryService._calculate(
        _report(required, actual), _profile(), Decimal("0"), required_override=override
    )


def test_group_required_days_uses_mode_and_ignores_truncated_new_hire() -> None:
    """三人 23、新人 9 → 全组标准 23；截短值不能污染标准。"""
    rows = [
        (_report(23, 17, oa_user_id=1), None),
        (_report(23, 22, oa_user_id=2), None),
        (_report(9, 9, oa_user_id=3), None),
        (_report(23, 18, oa_user_id=4), None),
    ]
    assert SalaryService._group_required_days(rows) == {GROUP_ID: 23.0}


def test_group_required_days_is_per_group() -> None:
    rows = [
        (_report(23, 17, group_id="A", oa_user_id=1), None),
        (_report(23, 18, group_id="A", oa_user_id=2), None),
        (_report(9, 9, group_id="A", oa_user_id=3), None),
        (_report(21, 20, group_id="B", oa_user_id=4), None),
        (_report(21, 19, group_id="B", oa_user_id=5), None),
    ]
    assert SalaryService._group_required_days(rows) == {"A": 23.0, "B": 21.0}


def test_group_required_days_skips_ambiguous_group() -> None:
    """应出勤各不相同时没有明确众数，不做抬平，避免误改正常员工金额。"""
    rows = [
        (_report(23, 17, oa_user_id=1), None),
        (_report(22, 21, oa_user_id=2), None),
        (_report(9, 9, oa_user_id=3), None),
    ]
    assert SalaryService._group_required_days(rows) == {}


def test_new_hire_prorated_by_group_required() -> None:
    """23 / 18 → 4000 × 18 ÷ 23 = 3130.43，不是整月 4000；18 < 23 故不发全勤奖。"""
    result = _calc(9, 18, override=23.0)
    assert result["required_days"] == 23.0
    assert result["actual_days"] == 18.0
    assert result["prorated_base"] == Decimal("3130.43")
    assert result["full_attendance_award"] == Decimal("0.00")
    assert result["gross_amount"] == Decimal("3130.43")
    assert result["net_amount"] == Decimal("3130.43")


def test_defect_before_fix_would_pay_full_month() -> None:
    """不传 override 时保留旧行为（9/9 = 全额），用于证明缺陷成因。"""
    result = _calc(9, 9)
    assert result["prorated_base"] == Decimal("4000.00")


def test_group_override_never_changes_normal_employees() -> None:
    """同组三人传不传 override 结果完全一致（Q2「只有一条需要修正」）。"""
    for actual in (17, 22, 18):
        legacy = SalaryService._calculate(_report(23, actual), _profile(), Decimal("0"))
        fixed = _calc(23, actual, override=23.0)
        assert fixed == legacy
    assert _calc(23, 17, override=23.0)["prorated_base"] == Decimal("2956.52")


def test_group_override_only_raises_never_lowers() -> None:
    """个人值高于全组标准（休息日重算跳过减 1）时保持个人值，不被压平。"""
    result = _calc(24, 22, override=23.0)
    assert result["required_days"] == 24.0
    assert result["prorated_base"] == Decimal("3666.67")


def test_dismissal_plan_keeps_personal_required() -> None:
    """ADR-0016：离职结算单不套用全组统一 R，仍按个人月报值原样计算（不按整月补齐）。"""
    result = SalaryService._calculate(_report(15, 14), _profile(), Decimal("0"))
    assert result["required_days"] == 15.0
    assert result["prorated_base"] == Decimal("3733.33")
