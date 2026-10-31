"""必要人数の初期値（まだ設定していない日に入れておく値）.

ふだんの体制（リーダー1名・チェッカー1名・クリーナー5名）を初期値にして、
変わる日だけ直せばよいようにする。保存済みの日の値は変えない。
ロールは role_code で引く（DB の role_id を決め打ちしない）。
"""

from collections.abc import Iterable, Mapping

from src.models import RoleRequirementInput

# 役割ごとの最低人数の初期値（role_code -> 人数）
DEFAULT_ROLE_MINIMUMS: dict[str, int] = {"LEADER": 1, "CHECKER": 1, "CLEANER": 5}
# 最低必要人数（合計）の初期値. 役割の最低人数の合計とそろえる
DEFAULT_REQUIRED_TOTAL = sum(DEFAULT_ROLE_MINIMUMS.values())


def default_role_counts(roles: Iterable[Mapping]) -> dict[int, int]:
    """role_id -> 初期の最低人数. 初期値のない役割は 0."""
    return {role["role_id"]: DEFAULT_ROLE_MINIMUMS.get(role["role_code"], 0) for role in roles}


def default_role_requirements(work_date: str, roles: Iterable[Mapping]) -> list[RoleRequirementInput]:
    """その日の役割ごとの最低人数（初期値）. 0人の役割は行を作らない."""
    return [
        RoleRequirementInput(work_date, role_id, count)
        for role_id, count in default_role_counts(roles).items()
        if count > 0
    ]


def describe_default_roles(roles: Iterable[Mapping]) -> str:
    """'リーダー1名・チェッカー1名・クリーナー5名' のような説明."""
    parts = [
        f"{role['role_name']}{DEFAULT_ROLE_MINIMUMS[role['role_code']]}名"
        for role in roles
        if DEFAULT_ROLE_MINIMUMS.get(role["role_code"], 0) > 0
    ]
    return "・".join(parts)
