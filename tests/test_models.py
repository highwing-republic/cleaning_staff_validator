import dataclasses

import pytest

from src.models import (
    AssignmentResult,
    DailyRequirementInput,
    LockedAssignmentInput,
    MonthlyConditionInput,
    PrecheckResult,
    PreferenceInput,
    RoleRequirementInput,
    SchedulerInput,
    SchedulerResult,
    StaffInput,
    StageObjectiveResult,
    ValidationError,
)


def _staff(**kw):
    base = dict(
        staff_id=1,
        staff_name="山田",
        role_id=3,
        daily_work_minutes=480,
        max_consecutive_days=5,
        weekday_availability={w: True for w in range(7)},
    )
    base.update(kw)
    return StaffInput(**base)


def test_staff_input_defaults_and_weekday():
    s = _staff(weekday_availability={0: True, 1: False})
    assert s.active is True
    assert s.is_available_on(0) is True
    assert s.is_available_on(1) is False
    assert s.is_available_on(6) is False  # 欠落はFalse
    assert s.skill_level == 3  # デフォルト


def test_staff_input_skill_level_override():
    s = _staff(skill_level=5)
    assert s.skill_level == 5


def test_inputs_are_frozen():
    s = _staff()
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.staff_name = "x"


def test_monthly_condition_defaults():
    m = MonthlyConditionInput(staff_id=1, year_month="2026-10", target_monthly_minutes=9600)
    assert m.min_monthly_minutes is None
    assert m.max_monthly_minutes is None
    assert m.carryover_consecutive_days == 0


def test_daily_and_role_requirement():
    d = DailyRequirementInput(work_date="2026-10-01", required_total_staff=8)
    assert d.max_total_staff is None
    assert d.occupancy_rate is None
    assert d.note is None
    r = RoleRequirementInput(work_date="2026-10-01", role_id=1, required_count=1)
    assert r.required_count == 1


@pytest.mark.parametrize("ptype", ["UNAVAILABLE", "PREFER_OFF", "PREFER_WORK"])
def test_preference_valid(ptype):
    assert PreferenceInput(1, "2026-10-01", ptype).preference_type == ptype


def test_preference_invalid():
    with pytest.raises(ValueError):
        PreferenceInput(1, "2026-10-01", "HOLIDAY")


def test_locked_assignment():
    assert LockedAssignmentInput(1, "2026-10-01", True).is_working is True


def test_scheduler_input_defaults_are_independent():
    a = SchedulerInput(year_month="2026-10")
    b = SchedulerInput(year_month="2026-11")
    assert a.staff == [] and a.locked_assignments == []
    assert a.staff is not b.staff


def test_scheduler_result_required_fields():
    names = {f.name for f in dataclasses.fields(SchedulerResult)}
    assert {
        "status",
        "assignments",
        "completed_stage",
        "objective_overstaff",
        "objective_target_deviation",
        "objective_prefer_off",
        "objective_prefer_work",
    } <= names


def test_scheduler_result_infeasible_defaults():
    r = SchedulerResult(status="INFEASIBLE")
    assert r.assignments == []
    assert r.completed_stage == 0
    assert r.objective_overstaff is None
    assert r.objective_prefer_work is None
    assert r.has_solution is False


def test_scheduler_result_feasible():
    r = SchedulerResult(
        status="FEASIBLE",
        assignments=[AssignmentResult(1, "2026-10-01", True)],
        completed_stage=2,
        objective_overstaff=0,
        objective_target_deviation=3,
        stage_results=[
            StageObjectiveResult(1, "OPTIMAL", 0),
            StageObjectiveResult(2, "OPTIMAL", 3),
            StageObjectiveResult(3, "UNKNOWN"),
        ],
    )
    assert r.has_solution is True
    assert r.objective_prefer_off is None


@pytest.mark.parametrize("status", ["DONE", "optimal", ""])
def test_scheduler_result_invalid_status(status):
    with pytest.raises(ValueError):
        SchedulerResult(status=status)


@pytest.mark.parametrize("stage", [-1, 6])
def test_scheduler_result_invalid_completed_stage(stage):
    with pytest.raises(ValueError):
        SchedulerResult(status="OPTIMAL", completed_stage=stage)


def test_scheduler_result_v14_fields_default_none():
    r = SchedulerResult(status="OPTIMAL", completed_stage=5)
    assert r.objective_max_deviation is None
    assert r.objective_max_overstaff is None


@pytest.mark.parametrize("stage", [1, 5])
def test_stage_objective_valid_stage_range(stage):
    assert StageObjectiveResult(stage, "OPTIMAL", 0).stage == stage


@pytest.mark.parametrize("stage", [0, 6])
def test_stage_objective_invalid_stage(stage):
    with pytest.raises(ValueError):
        StageObjectiveResult(stage, "OPTIMAL", 0)


def test_stage_objective_invalid_status():
    with pytest.raises(ValueError):
        StageObjectiveResult(1, "MODEL_INVALID", 0)


def test_precheck_result():
    assert PrecheckResult().is_ok is True
    err = ValidationError(code="PC05", message="1名不足", work_date="2026-10-12")
    r = PrecheckResult(errors=[err])
    assert r.is_ok is False
    assert r.errors[0].staff_id is None
