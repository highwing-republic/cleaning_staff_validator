"""日別検証結果の表示用文字列（画面・Excel共通）."""

from src.models import DailyStaffingResult

REQUIREMENT_MISSING_LABEL = "要件未設定"
NOT_APPLICABLE = "-"


def format_count_cell(known: int, required: int, possible: int | None) -> str:
    """'確定 / 必要'. 確定が不足する場合は '（最大n）' を付ける. 例: '0 / 1（最大1）'."""
    text = f"{known} / {required}"
    if known < required and possible is not None:
        text += f"（最大{possible}）"
    return text


def format_role_cell(day: DailyStaffingResult, role_id: int) -> str:
    known = day.actual_roles.get(role_id, 0)
    if not day.requirement_defined:
        return f"{known} / {NOT_APPLICABLE}"
    return format_count_cell(
        known, day.required_roles.get(role_id, 0), day.possible_roles.get(role_id)
    )


def format_skill_cell(day: DailyStaffingResult) -> str:
    """スキル条件あり: 'Lv4+ 1 / 2（最大2）'. 条件なし・要件未設定: '-'."""
    if (
        not day.requirement_defined
        or day.required_skill_count <= 0
        or day.required_skill_level is None
    ):
        return NOT_APPLICABLE
    return f"Lv{day.required_skill_level}+ " + format_count_cell(
        day.actual_skill_count, day.required_skill_count, day.possible_skill_count
    )
