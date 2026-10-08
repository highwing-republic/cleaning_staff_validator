import dataclasses

import pytest

from src import models
from src.models import (
    AttendanceImportRecord,
    AttendanceShiftInput,
    DailyRequirementInput,
    RoleRequirementInput,
    StaffInput,
    ValidationError,
)


def _staff(**kw):
    base = dict(staff_id=1, employee_code="0015", staff_name="山田", role_id=3)
    base.update(kw)
    return StaffInput(**base)


def test_staff_input_defaults():
    s = _staff()
    assert s.employee_code == "0015"  # 先頭0を保つ文字列
    assert s.skill_level == 3
    assert s.department is None
    assert s.active is True


def test_staff_input_has_no_generation_fields():
    """StaffInputに持たせないもの.

    - daily_work_minutes / standard_work_minutes: 開始・終了から計算する（二重保持しない）
    - weekday_availability: 通常勤務曜日は staff_weekday_patterns（別テーブル）で持つ
    - special_skill_ids: 特殊スキルは多対多なので StaffDetail 側で扱う
    max_consecutive_days はPhase 5でスタッフ個別の通常勤務条件として持つ。
    """
    names = {f.name for f in dataclasses.fields(StaffInput)}
    assert not names & {
        "daily_work_minutes",
        "standard_work_minutes",
        "weekday_availability",
        "special_skill_ids",
    }


def test_staff_input_has_standard_work_conditions():
    names = {f.name for f in dataclasses.fields(StaffInput)}
    assert {
        "standard_start_time",
        "standard_end_time",
        "target_days_per_week",
        "max_days_per_period",
        "max_consecutive_days",
    } <= names


def test_staff_input_standard_conditions_default_to_none():
    s = _staff()
    assert s.standard_start_time is None
    assert s.standard_end_time is None
    assert s.target_days_per_week is None
    assert s.max_days_per_period is None
    assert s.max_consecutive_days is None


def test_inputs_are_frozen():
    s = _staff()
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.staff_name = "x"


def test_daily_and_role_requirement_defaults():
    d = DailyRequirementInput(work_date="2026-10-01", required_total_staff=8)
    assert d.max_total_staff is None
    assert d.occupancy_rate is None
    assert d.note is None
    assert d.required_skill_level is None
    assert d.required_skill_count == 0
    r = RoleRequirementInput(work_date="2026-10-01", role_id=1, required_count=1)
    assert r.required_count == 1


def test_attendance_shift_unmatched_defaults():
    s = AttendanceShiftInput(
        employee_code="0099",
        work_date="2026-09-01",
        raw_shift="謎の値",
        shift_type="UNKNOWN",
        available_for_cleaning=False,
    )
    assert s.staff_id is None
    assert s.start_minutes is None and s.end_minutes is None
    assert s.raw_shift == "謎の値"


def test_attendance_shift_invalid_type():
    with pytest.raises(ValueError):
        AttendanceShiftInput("1", "2026-09-01", "", "HOLIDAY", False)


def test_attendance_import_record_status():
    rec = AttendanceImportRecord(1, "2026-09", "a.csv", "2026-09-26T10:00:00", 3, 10, 1, "ACTIVE")
    assert rec.status == "ACTIVE"
    with pytest.raises(ValueError):
        AttendanceImportRecord(1, "2026-09", "a.csv", "t", 3, 10, 1, "DRAFT")


def test_validation_error_defaults():
    err = ValidationError(code="X", message="m")
    assert err.staff_id is None and err.work_date is None and err.role_id is None


def test_generation_models_removed():
    for name in (
        "SchedulerInput",
        "SchedulerResult",
        "StageObjectiveResult",
        "AssignmentResult",
        "MonthlyConditionInput",
        "PreferenceInput",
        "LockedAssignmentInput",
        "PrecheckResult",
    ):
        assert not hasattr(models, name), name
