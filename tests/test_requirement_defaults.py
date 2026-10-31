"""必要人数の初期値（リーダー1名・チェッカー1名・クリーナー5名）."""

from src.models import RoleRequirementInput
from src.requirement_defaults import (
    DEFAULT_REQUIRED_TOTAL,
    default_role_counts,
    default_role_requirements,
    describe_default_roles,
)

ROLES = [
    {"role_id": 1, "role_code": "LEADER", "role_name": "リーダー"},
    {"role_id": 2, "role_code": "CHECKER", "role_name": "チェッカー"},
    {"role_id": 3, "role_code": "CLEANER", "role_name": "クリーナー"},
]


def test_total_matches_the_role_minimums():
    assert DEFAULT_REQUIRED_TOTAL == 7


def test_counts_are_looked_up_by_role_code_not_id():
    shuffled = [{"role_id": 9, "role_code": "CLEANER", "role_name": "クリーナー"},
                {"role_id": 4, "role_code": "LEADER", "role_name": "リーダー"}]
    assert default_role_counts(shuffled) == {9: 5, 4: 1}


def test_unknown_role_gets_zero_and_no_row():
    roles = [*ROLES, {"role_id": 7, "role_code": "TRAINEE", "role_name": "研修"}]
    assert default_role_counts(roles)[7] == 0
    rows = default_role_requirements("2026-11-02", roles)
    assert RoleRequirementInput("2026-11-02", 7, 0) not in rows
    assert rows == [
        RoleRequirementInput("2026-11-02", 1, 1),
        RoleRequirementInput("2026-11-02", 2, 1),
        RoleRequirementInput("2026-11-02", 3, 5),
    ]


def test_description():
    assert describe_default_roles(ROLES) == "リーダー1名・チェッカー1名・クリーナー5名"
