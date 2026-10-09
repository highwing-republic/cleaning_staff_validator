"""勤務表出力・検証結果の表示用文字列（画面・Excel共通）.

色に頼らず文字で分かるようにする（モノクロ印刷でも確定/下書き/不足が読めるようにするため）。
"""

from src.constants import (
    VALIDATION_STATUS_ERROR,
    VALIDATION_STATUS_INFO,
    VALIDATION_STATUS_OK,
    VALIDATION_STATUS_WARNING,
)
from src.models import ScheduleAssignmentRecord, ScheduleDayValidationResult
from src.period_utils import format_date_short
from src.validation_display import format_role_cell, format_skill_cell

STATUS_FINALIZED_LABEL = "確定"
STATUS_DRAFT_LABEL = "下書き"
STATUS_MISSING_LABEL = "未作成"
OFF_LABEL = "休"
NO_ROW_LABEL = "-"
NOT_APPLICABLE = "-"
DRAFT_NOTICE = "※下書きの日付を含みます"

SEVERITY_LABELS = {
    VALIDATION_STATUS_ERROR: "ERROR",
    VALIDATION_STATUS_WARNING: "WARNING",
    VALIDATION_STATUS_INFO: "INFO",
    VALIDATION_STATUS_OK: "OK",
}

SEVERITY_MARKS = {
    VALIDATION_STATUS_ERROR: "🔴 ERROR",
    VALIDATION_STATUS_WARNING: "🟡 WARNING",
    VALIDATION_STATUS_INFO: "🔵 INFO",
    VALIDATION_STATUS_OK: "🟢 OK",
}

_SEVERITY_ORDER = {
    VALIDATION_STATUS_ERROR: 0,
    VALIDATION_STATUS_WARNING: 1,
    VALIDATION_STATUS_INFO: 2,
    VALIDATION_STATUS_OK: 3,
}


def severity_sort_key(issue) -> tuple[int, str]:
    """ERROR → WARNING → INFO の順、同じ重大度内は日付順."""
    return (_SEVERITY_ORDER.get(issue.severity, 9), issue.work_date)


def format_schedule_status(day: ScheduleDayValidationResult) -> str:
    """確定 / 下書き / 未作成."""
    if not day.exists:
        return STATUS_MISSING_LABEL
    return STATUS_FINALIZED_LABEL if day.is_finalized else STATUS_DRAFT_LABEL


def format_day_state(day: ScheduleDayValidationResult) -> str:
    """'確定・OK' のように、作成状態と判定をまとめて表す."""
    if not day.exists:
        return STATUS_MISSING_LABEL
    return f"{format_schedule_status(day)}・{SEVERITY_LABELS.get(day.status, day.status)}"


def format_date_with_status(day: ScheduleDayValidationResult) -> str:
    """'10/20(火) 確定' のような列見出し（印刷時に状態が分かるようにする）."""
    return f"{format_date_short(day.work_date)} {format_schedule_status(day)}"


def format_export_cell(
    assignment: ScheduleAssignmentRecord | None, day_exists: bool
) -> str:
    """勤務表セル. 固定・手修正の印は出さない（調整用の内部情報なので）."""
    if not day_exists:
        return STATUS_MISSING_LABEL
    if assignment is None:
        return NO_ROW_LABEL
    if not assignment.is_working:
        return OFF_LABEL
    return f"{assignment.start_time}-{assignment.end_time}"


def format_required(day: ScheduleDayValidationResult) -> str:
    if day.requirement is None:
        return NOT_APPLICABLE
    return str(day.requirement.required_total_staff)


def format_scheduled(day: ScheduleDayValidationResult) -> str:
    return NOT_APPLICABLE if not day.exists else str(day.scheduled_staff)


def format_reserved_rooms(day: ScheduleDayValidationResult) -> str:
    rooms = day.reserved_rooms
    return NOT_APPLICABLE if rooms is None else str(rooms)


def format_staff_count(day: ScheduleDayValidationResult) -> str:
    """人数の判定. '1名不足' / 'OK' / '-'."""
    if not day.exists or day.staffing is None or not day.staffing.requirement_defined:
        return NOT_APPLICABLE
    shortage = day.staff_shortage
    if shortage:
        return f"{shortage}名不足"
    max_total = day.staffing.max_total_staff
    if max_total is not None and day.staffing.actual_cleaning_staff > max_total:
        return f"{day.staffing.actual_cleaning_staff - max_total}名超過"
    return "OK"


def format_role(day: ScheduleDayValidationResult, role_id: int) -> str:
    """ロール別の充足。既存の検証結果表示をそのまま使う."""
    if not day.exists or day.staffing is None:
        return NOT_APPLICABLE
    return format_role_cell(day.staffing, role_id)


def format_skill(day: ScheduleDayValidationResult) -> str:
    if not day.exists or day.staffing is None:
        return NOT_APPLICABLE
    return format_skill_cell(day.staffing)
