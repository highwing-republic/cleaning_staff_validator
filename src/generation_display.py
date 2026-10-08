"""シフト生成結果の表示用文字列.

不足がある日は目立たせるが「生成失敗」扱いにはしない（不足も結果の一部）。
要件未設定の日は0名配置が正常なので、不足と混同させない表示にする。
"""

from src.constants import (
    GENERATION_STATUS_OK,
    GENERATION_STATUS_REQUIREMENT_MISSING,
    GENERATION_STATUS_SHORTAGE,
)
from src.models import DailyGenerationResult

OFF_LABEL = "休"
NOT_APPLICABLE = "-"
REQUIREMENT_MISSING_LABEL = "要件未設定"
STATUS_LABELS = {
    GENERATION_STATUS_OK: "充足",
    GENERATION_STATUS_SHORTAGE: "不足",
    GENERATION_STATUS_REQUIREMENT_MISSING: REQUIREMENT_MISSING_LABEL,
}


def format_status(day: DailyGenerationResult) -> str:
    return STATUS_LABELS.get(day.status, day.status)


def format_assignment_cell(start_time: str | None, end_time: str | None, is_working: bool) -> str:
    """生成結果グリッドの1セル. 出勤なら実効時間、休みなら「休」."""
    if not is_working:
        return OFF_LABEL
    if start_time and end_time:
        return f"{start_time}-{end_time}"
    return OFF_LABEL


def format_required(day: DailyGenerationResult) -> str:
    if not day.requirement_is_set:
        return NOT_APPLICABLE
    return str(day.required_total_staff)


def format_staff_shortage(day: DailyGenerationResult) -> str:
    if not day.requirement_is_set:
        return NOT_APPLICABLE
    return str(day.staff_shortage)


def format_role_shortages(day: DailyGenerationResult, role_names: dict[int, str]) -> str:
    """'リーダー 1名' のように不足しているロールだけ並べる."""
    if not day.role_shortages:
        return NOT_APPLICABLE
    return " / ".join(
        f"{role_names.get(role_id, role_id)} {count}名"
        for role_id, count in sorted(day.role_shortages.items())
    )


def format_skill_shortage(day: DailyGenerationResult) -> str:
    if day.skill_shortage <= 0:
        return NOT_APPLICABLE
    return f"{day.skill_shortage}名"


def format_shortage_summary(day: DailyGenerationResult, role_names: dict[int, str]) -> str:
    """不足日の1行説明（例 '1名不足、リーダー 1名不足'）."""
    parts: list[str] = []
    if day.staff_shortage > 0:
        parts.append(f"{day.staff_shortage}名不足")
    for role_id, count in sorted(day.role_shortages.items()):
        parts.append(f"{role_names.get(role_id, role_id)} {count}名不足")
    if day.skill_shortage > 0:
        parts.append(f"スキル条件 {day.skill_shortage}名不足")
    return "、".join(parts)
