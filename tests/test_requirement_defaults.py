"""必要人数の初期値（平日: リーダー1・チェッカー1・クリーナー6 ／ 土日祝: クリーナー5）."""

from src.models import RoleRequirementInput
from src.requirement_defaults import (
    WEEKDAY_REQUIRED_TOTAL,
    WEEKEND_REQUIRED_TOTAL,
    default_role_counts,
    default_role_requirements,
    describe_default_roles,
    is_weekend_or_holiday,
    required_total_for,
)

ROLES = [
    {"role_id": 1, "role_code": "LEADER", "role_name": "リーダー"},
    {"role_id": 2, "role_code": "CHECKER", "role_name": "チェッカー"},
    {"role_id": 3, "role_code": "CLEANER", "role_name": "クリーナー"},
]
MONDAY, FRIDAY, SATURDAY, SUNDAY = "2026-11-02", "2026-11-06", "2026-11-07", "2026-11-08"
HOLIDAY = "2026-11-03"  # 文化の日（火曜）


def test_totals_match_the_role_minimums():
    assert (WEEKDAY_REQUIRED_TOTAL, WEEKEND_REQUIRED_TOTAL) == (8, 7)


def test_weekday_and_weekend_totals():
    assert required_total_for(MONDAY) == required_total_for(FRIDAY) == 8
    assert required_total_for(SATURDAY) == required_total_for(SUNDAY) == 7


def test_holidays_use_the_weekend_defaults():
    assert is_weekend_or_holiday(HOLIDAY) and not is_weekend_or_holiday(MONDAY)
    assert required_total_for(HOLIDAY) == 7
    assert default_role_counts(HOLIDAY, ROLES) == {1: 1, 2: 1, 3: 5}


def test_weekend_has_five_cleaners():
    assert default_role_counts(MONDAY, ROLES) == {1: 1, 2: 1, 3: 6}
    assert default_role_counts(SUNDAY, ROLES) == {1: 1, 2: 1, 3: 5}


def test_counts_are_looked_up_by_role_code_not_id():
    shuffled = [{"role_id": 9, "role_code": "CLEANER", "role_name": "クリーナー"},
                {"role_id": 4, "role_code": "LEADER", "role_name": "リーダー"}]
    assert default_role_counts(MONDAY, shuffled) == {9: 6, 4: 1}


def test_unknown_role_gets_zero_and_no_row():
    roles = [*ROLES, {"role_id": 7, "role_code": "TRAINEE", "role_name": "研修"}]
    assert default_role_counts(MONDAY, roles)[7] == 0
    assert default_role_requirements(SATURDAY, roles) == [
        RoleRequirementInput(SATURDAY, 1, 1),
        RoleRequirementInput(SATURDAY, 2, 1),
        RoleRequirementInput(SATURDAY, 3, 5),
    ]


def test_description():
    assert describe_default_roles(ROLES) == (
        "平日 計8名（リーダー1名・チェッカー1名・クリーナー6名）、"
        "土日祝 計7名（リーダー1名・チェッカー1名・クリーナー5名）"
    )
