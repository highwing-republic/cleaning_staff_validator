"""T43 HC11 固定出勤 / T44 HC12 固定休日."""

from scheduler_helpers import (
    YM,
    assert_hard_constraints,
    cond,
    day,
    grid,
    make_input,
    staff,
)
from src.models import LockedAssignmentInput, PreferenceInput
from src.scheduler import build_model, generate_schedule


def test_locked_work_and_off_are_kept_against_preferences():
    inp = make_input(
        [staff(1), staff(2), staff(3)],
        conditions=[cond(1, 9), cond(2, 9), cond(3, 10)],
        prefs=[
            PreferenceInput(1, day(5), "PREFER_OFF"),
            PreferenceInput(2, day(6), "PREFER_WORK"),
        ],
        locks=[
            LockedAssignmentInput(1, day(5), True),
            LockedAssignmentInput(2, day(6), False),
        ],
    )
    result = generate_schedule(inp)
    assert_hard_constraints(inp, result)
    g = grid(result)
    assert g[1, day(5)] is True
    assert g[2, day(6)] is False
    assert result.objective_prefer_off == 1
    assert result.objective_prefer_work == 1


def test_locked_work_beyond_objective_preference():
    # 必要人数1の日に2名固定出勤 → 過剰配置1が最小値
    inp = make_input(
        [staff(1), staff(2)],
        conditions=[cond(1, 14), cond(2, 14)],
        locks=[LockedAssignmentInput(1, day(7), True), LockedAssignmentInput(2, day(7), True)],
    )
    result = generate_schedule(inp)
    assert_hard_constraints(inp, result)
    assert result.objective_overstaff == 1


def test_locked_off_for_all_is_infeasible():
    inp = make_input(
        [staff(1), staff(2)],
        locks=[LockedAssignmentInput(1, day(7), False), LockedAssignmentInput(2, day(7), False)],
    )
    assert generate_schedule(inp).status == "INFEASIBLE"


def test_locked_work_on_unavailable_is_infeasible_not_relaxed():
    inp = make_input(
        [staff(1), staff(2)],
        prefs=[PreferenceInput(1, day(7), "UNAVAILABLE")],
        locks=[LockedAssignmentInput(1, day(7), True)],
    )
    assert generate_schedule(inp).status == "INFEASIBLE"


def test_locked_work_on_weekday_off_is_infeasible():
    inp = make_input(
        [staff(1, off_weekdays=(6,)), staff(2)],
        locks=[LockedAssignmentInput(1, day(1), True)],  # 2/1 は日曜
    )
    assert generate_schedule(inp).status == "INFEASIBLE"


def test_locks_for_inactive_staff_and_other_months_are_ignored():
    inp = make_input(
        [staff(1), staff(2), staff(3, active=False)],
        conditions=[cond(1, 14), cond(2, 14)],
        locks=[
            LockedAssignmentInput(3, day(7), True),
            LockedAssignmentInput(1, "2026-03-01", True),
        ],
    )
    sm = build_model(inp)
    assert sm.locks == {}
    result = generate_schedule(inp)
    assert_hard_constraints(inp, result)
    assert inp.year_month == YM
