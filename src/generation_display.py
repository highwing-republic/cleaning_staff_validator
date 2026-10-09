"""シフト生成結果の表示用文字列.

不足がある日は目立たせるが「生成失敗」扱いにはしない（不足も結果の一部）。
要件未設定の日は0名配置が正常なので、不足と混同させない表示にする。
"""

from src.constants import (
    GENERATION_STATUS_OK,
    GENERATION_STATUS_REQUIREMENT_MISSING,
    GENERATION_STATUS_SHORTAGE,
)
from src.constants import SCHEDULE_SOURCE_MANUAL
from src.models import (
    DailyGenerationResult,
    ScheduleAssignmentRecord,
    ScheduleChange,
    ScheduleDayView,
    ScheduleGenerationResult,
    StaffGenerationSummary,
)

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


# ---------------------------------------------------------------------------
# スタッフ別の勤務状況（希望休の尊重・目標勤務日数への近さ）
# ---------------------------------------------------------------------------


def format_target_days(summary: StaffGenerationSummary) -> str:
    """期間に換算した目安日数（例 '6.0日' / '4.3日'）. 目標未設定なら '-'.

    Solver内部は整数スケールだが、画面にはscaled値を出さず日数へ戻して表示する。
    """
    if not summary.has_target:
        return NOT_APPLICABLE
    return f"{summary.target_days:.1f}日"


def format_scheduled_days(summary: StaffGenerationSummary) -> str:
    return f"{summary.scheduled_days}日"


def format_prefer_off_respect(result: ScheduleGenerationResult) -> str:
    """希望休の尊重状況（例 '8 / 9'）. 申請が0件なら '-'."""
    requested = result.prefer_off_requested_total
    if requested <= 0:
        return NOT_APPLICABLE
    return f"{result.prefer_off_respected_total} / {requested}"


# ---------------------------------------------------------------------------
# 現在の勤務表（Phase 10）
# ---------------------------------------------------------------------------

SCHEDULE_MISSING_LABEL = "未作成"
SCHEDULE_DRAFT_LABEL = "下書き"
SCHEDULE_FINALIZED_LABEL = "確定"
LOCK_MARK = "🔒"
MANUAL_MARK = "※"


def format_schedule_day_status(view: ScheduleDayView) -> str:
    """勤務表の日別状態（未作成 / 下書き / 確定）."""
    if not view.exists:
        return SCHEDULE_MISSING_LABEL
    if view.is_finalized:
        return SCHEDULE_FINALIZED_LABEL
    return SCHEDULE_DRAFT_LABEL


def format_schedule_cell(
    assignment: ScheduleAssignmentRecord | None, day_is_finalized: bool = False
) -> str:
    """勤務表グリッドの1セル.

    固定は 🔒、手修正は ※ を付ける。勤務表がない日は「未作成」。
    確定日は日単位で読み取り専用なので、セルごとの印は付けない。
    """
    if assignment is None:
        return SCHEDULE_MISSING_LABEL

    if assignment.is_working and assignment.start_time and assignment.end_time:
        text = f"{assignment.start_time}-{assignment.end_time}"
    else:
        text = OFF_LABEL

    marks = ""
    if assignment.locked and not day_is_finalized:
        marks += LOCK_MARK
    if assignment.source == SCHEDULE_SOURCE_MANUAL:
        marks += MANUAL_MARK
    return f"{marks}{text}" if marks else text


def format_schedule_change(change: ScheduleChange) -> str:
    """再生成プレビューの1行（例 '休 → 09:00-15:30'）."""
    before = OFF_LABEL if not change.before_is_working else "出勤"
    if change.after_is_working and change.after_start_time and change.after_end_time:
        after = f"{change.after_start_time}-{change.after_end_time}"
    else:
        after = OFF_LABEL
    return f"{before} → {after}"


def format_work_state(is_working: bool) -> str:
    return "出勤" if is_working else OFF_LABEL

