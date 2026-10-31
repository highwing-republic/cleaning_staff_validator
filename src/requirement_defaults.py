"""必要人数の初期値（まだ設定していない日に入れておく値）.

ふだんの体制を初期値にして、変わる日だけ直せばよいようにする。保存済みの日の値は変えない。
- 平日: リーダー1名・チェッカー1名・クリーナー6名（計8名）
- 土日祝: リーダー1名・チェッカー1名・クリーナー5名（計7名）. 祝日は土日と同じ扱い
ロールは role_code で引く（DB の role_id を決め打ちしない）。

役割の内訳は必要人数（合計）から自動で決められる（auto_role_counts）:
リーダー1名・チェッカー1名・残りをクリーナー（8名→1・1・6、7名→1・1・5）。
初期値の内訳もこの決め方と一致する。
"""

from collections.abc import Iterable, Mapping
from datetime import date

from src.jp_holidays import is_holiday
from src.models import RoleRequirementInput

# 役割ごとの最低人数の初期値（role_code -> 人数）
WEEKDAY_ROLE_MINIMUMS: dict[str, int] = {"LEADER": 1, "CHECKER": 1, "CLEANER": 6}
# 土日・祝日
WEEKEND_ROLE_MINIMUMS: dict[str, int] = {"LEADER": 1, "CHECKER": 1, "CLEANER": 5}
# 最低必要人数（合計）の初期値. 役割の最低人数の合計とそろえる
WEEKDAY_REQUIRED_TOTAL = sum(WEEKDAY_ROLE_MINIMUMS.values())
WEEKEND_REQUIRED_TOTAL = sum(WEEKEND_ROLE_MINIMUMS.values())


def is_weekend_or_holiday(work_date: str) -> bool:
    """土日・祝日の初期値を使う日か（土曜・日曜・祝日）."""
    return date.fromisoformat(work_date).weekday() >= 5 or is_holiday(work_date)


def role_minimums_for(work_date: str) -> dict[str, int]:
    return WEEKEND_ROLE_MINIMUMS if is_weekend_or_holiday(work_date) else WEEKDAY_ROLE_MINIMUMS


def required_total_for(work_date: str) -> int:
    """その日の最低必要人数の初期値（平日8名・土日祝7名）."""
    return WEEKEND_REQUIRED_TOTAL if is_weekend_or_holiday(work_date) else WEEKDAY_REQUIRED_TOTAL


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
    """'平日 計8名（リーダー1名・チェッカー1名・クリーナー6名）、土日祝 計7名（…クリーナー5名）'."""
    roles = list(roles)
    return (
        f"平日 計{WEEKDAY_REQUIRED_TOTAL}名（{_describe(WEEKDAY_ROLE_MINIMUMS, roles)}）、"
        f"土日祝 計{WEEKEND_REQUIRED_TOTAL}名（{_describe(WEEKEND_ROLE_MINIMUMS, roles)}）"
    )


AUTO_LEADER_CODE = "LEADER"
AUTO_CHECKER_CODE = "CHECKER"
AUTO_CLEANER_CODE = "CLEANER"


def auto_role_counts(total: int, roles: Iterable[Mapping]) -> dict[int, int]:
    """必要人数（合計）から決める role_id -> 人数.

    リーダー1名（1名以上のとき）・チェッカー1名（2名以上のとき）・残りをクリーナー。
    ほかの役割は 0。
    """
    total = max(0, int(total or 0))
    by_code = {
        AUTO_LEADER_CODE: 1 if total >= 1 else 0,
        AUTO_CHECKER_CODE: 1 if total >= 2 else 0,
        AUTO_CLEANER_CODE: max(0, total - 2),
    }
    return {role["role_id"]: by_code.get(role["role_code"], 0) for role in roles}


def auto_role_requirements(work_date: str, total: int, roles: Iterable[Mapping]) -> list[RoleRequirementInput]:
    """必要人数から決めた役割ごとの人数. 0人の役割は行を作らない."""
    return [
        RoleRequirementInput(work_date, role_id, count)
        for role_id, count in auto_role_counts(total, roles).items()
        if count > 0
    ]


def _nonzero(counts: Mapping[int, int]) -> dict[int, int]:
    return {role_id: count for role_id, count in counts.items() if count}


def roles_follow_total(
    role_counts: Mapping[int, int], is_defined: bool, saved_total: int | None, roles: Iterable[Mapping]
) -> bool:
    """その日の役割の内訳が「必要人数から自動で決める」扱いか.

    - 役割の行がない日 → 自動
    - 保存済みの必要人数から自動で決めた内訳と同じ → 自動（人数を変えたら内訳も変える）
    - それ以外（詳しい設定で個別に変えた日など）→ 個別. 人数を変えても内訳はそのまま
    """
    if not role_counts:
        return True
    if not is_defined or saved_total is None:
        return False
    return _nonzero(role_counts) == _nonzero(auto_role_counts(saved_total, roles))


def describe_role_counts(role_counts: Mapping[int, int], roles: Iterable[Mapping]) -> str:
    """'リーダー1・チェッカー2・クリーナー3'（0人の役割は書かない. なければ '指定なし'）."""
    parts = [
        f"{role['role_name']}{role_counts[role['role_id']]}"
        for role in roles
        if role_counts.get(role["role_id"], 0) > 0
    ]
    return "・".join(parts) or "指定なし"
