"""予約・必要人数の表示用文字列.

「要件未設定」（daily_requirements に行がない）と「必要人数0」を画面上で
はっきり区別するための変換をここに集める。
予約室数も NULL（未入力・未確認）と 0（予約室数0）を区別して表示する。
"""

from src.models import DailyRequirementInput, DailyRequirementView

REQUIREMENT_UNDEFINED_LABEL = "要件未設定"
RESERVED_ROOMS_UNKNOWN_LABEL = "未入力"
NOT_APPLICABLE = "-"
NO_LIMIT_LABEL = "上限なし"


def format_reserved_rooms(reserved_rooms: int | None) -> str:
    """予約室数. 未入力と0室を区別する（0を「未入力」と見せない）."""
    if reserved_rooms is None:
        return RESERVED_ROOMS_UNKNOWN_LABEL
    return f"{reserved_rooms}室"


def format_required_staff(requirement: DailyRequirementInput | None) -> str:
    """必要清掃人数. 要件未設定の日は人数を出さない（0名と混同させない）."""
    if requirement is None:
        return REQUIREMENT_UNDEFINED_LABEL
    return f"{requirement.required_total_staff}名"


def format_max_staff(requirement: DailyRequirementInput | None) -> str:
    if requirement is None:
        return NOT_APPLICABLE
    if requirement.max_total_staff is None:
        return NO_LIMIT_LABEL
    return f"{requirement.max_total_staff}名"


def format_skill_condition(requirement: DailyRequirementInput | None) -> str:
    """スキル条件. 必要スキル人数0はスキル条件なし."""
    if (
        requirement is None
        or requirement.required_skill_count <= 0
        or requirement.required_skill_level is None
    ):
        return NOT_APPLICABLE
    return f"Lv{requirement.required_skill_level}+ {requirement.required_skill_count}名"


def format_role_condition(view: DailyRequirementView, role_id: int) -> str:
    """ロール別必要人数. 必要数0はそのRole条件なしなので '-' と表示する."""
    if not view.is_defined:
        return NOT_APPLICABLE
    count = view.role_counts.get(role_id, 0)
    return NOT_APPLICABLE if count <= 0 else f"{count}名"


def format_requirement_summary(view: DailyRequirementView) -> str:
    """1日分の要約（例 '予約8室 / 必要4名'）. 要件未設定ならその旨を返す."""
    if not view.is_defined:
        return REQUIREMENT_UNDEFINED_LABEL
    rooms = format_reserved_rooms(view.requirement.reserved_rooms)
    return f"予約{rooms} / 必要{view.requirement.required_total_staff}名"


def count_defined(views: list[DailyRequirementView]) -> int:
    return sum(1 for v in views if v.is_defined)


def count_undefined(views: list[DailyRequirementView]) -> int:
    return sum(1 for v in views if not v.is_defined)


def total_reserved_rooms(views: list[DailyRequirementView]) -> int:
    """期間内の予約室数合計. 未入力（None）の日は加算しない."""
    return sum(
        v.requirement.reserved_rooms
        for v in views
        if v.is_defined and v.requirement.reserved_rooms is not None
    )
