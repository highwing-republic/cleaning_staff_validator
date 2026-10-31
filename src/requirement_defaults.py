"""必要人数の初期値（まだ設定していない日に入れておく値）.

ふだんの体制を初期値にして、変わる日だけ直せばよいようにする。保存済みの日の値は変えない。
- 平日: リーダー1名・チェッカー1名・クリーナー5名（計7名）
- 土日: リーダー1名・チェッカー1名・クリーナー4名（計6名）
祝日はアプリで判定しない（平日の初期値になる。必要なら画面で直す）。
ロールは role_code で引く（DB の role_id を決め打ちしない）。
"""

from collections.abc import Iterable, Mapping
from datetime import date

from src.models import RoleRequirementInput

# 役割ごとの最低人数の初期値（role_code -> 人数）
WEEKDAY_ROLE_MINIMUMS: dict[str, int] = {"LEADER": 1, "CHECKER": 1, "CLEANER": 5}
WEEKEND_ROLE_MINIMUMS: dict[str, int] = {"LEADER": 1, "CHECKER": 1, "CLEANER": 4}
# 最低必要人数（合計）の初期値. 役割の最低人数の合計とそろえる
WEEKDAY_REQUIRED_TOTAL = sum(WEEKDAY_ROLE_MINIMUMS.values())
WEEKEND_REQUIRED_TOTAL = sum(WEEKEND_ROLE_MINIMUMS.values())


def is_weekend(work_date: str) -> bool:
    return date.fromisoformat(work_date).weekday() >= 5


def role_minimums_for(work_date: str) -> dict[str, int]:
    return WEEKEND_ROLE_MINIMUMS if is_weekend(work_date) else WEEKDAY_ROLE_MINIMUMS


def required_total_for(work_date: str) -> int:
    """その日の最低必要人数の初期値（平日7名・土日6名）."""
    return WEEKEND_REQUIRED_TOTAL if is_weekend(work_date) else WEEKDAY_REQUIRED_TOTAL


def default_role_counts(work_date: str, roles: Iterable[Mapping]) -> dict[int, int]:
    """その日の role_id -> 初期の最低人数. 初期値のない役割は 0."""
    minimums = role_minimums_for(work_date)
    return {role["role_id"]: minimums.get(role["role_code"], 0) for role in roles}


def default_role_requirements(work_date: str, roles: Iterable[Mapping]) -> list[RoleRequirementInput]:
    """その日の役割ごとの最低人数（初期値）. 0人の役割は行を作らない."""
    return [
        RoleRequirementInput(work_date, role_id, count)
        for role_id, count in default_role_counts(work_date, roles).items()
        if count > 0
    ]


def _describe(minimums: Mapping[str, int], roles: Iterable[Mapping]) -> str:
    parts = [
        f"{role['role_name']}{minimums[role['role_code']]}名"
        for role in roles
        if minimums.get(role["role_code"], 0) > 0
    ]
    return "・".join(parts)


def describe_default_roles(roles: Iterable[Mapping]) -> str:
    """'平日 計7名（リーダー1名・チェッカー1名・クリーナー5名）、土日 計6名（…クリーナー4名）'."""
    roles = list(roles)
    return (
        f"平日 計{WEEKDAY_REQUIRED_TOTAL}名（{_describe(WEEKDAY_ROLE_MINIMUMS, roles)}）、"
        f"土日 計{WEEKEND_REQUIRED_TOTAL}名（{_describe(WEEKEND_ROLE_MINIMUMS, roles)}）"
    )
