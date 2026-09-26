"""T30〜T36, T45〜T47: 構造・基本Hard Constraint・ステータス変換."""

import pytest
from ortools.sat.python import cp_model

from scheduler_helpers import (
    DATES,
    assert_hard_constraints,
    cond,
    day,
    make_input,
    staff,
    workers_on,
)
from src import scheduler
from src.models import PreferenceInput
from src.scheduler import build_model, generate_schedule


def test_build_model_creates_variables_for_active_staff_only():
    inp = make_input([staff(1), staff(2), staff(3, active=False)])
    sm = build_model(inp)
    assert [s.staff_id for s in sm.staff] == [1, 2]
    assert len(sm.x) == 2 * len(DATES)
    assert (3, DATES[0]) not in sm.x


def test_basic_solve_optimal():
    inp = make_input([staff(1), staff(2), staff(3)], conditions=[cond(i, 9) for i in (1, 2, 3)])
    result = generate_schedule(inp)
    assert result.status == "OPTIMAL"
    assert result.completed_stage == 5
    assert [r.stage for r in result.stage_results] == [1, 2, 3, 4, 5]
    assert len(result.assignments) == 3 * len(DATES)
    assert_hard_constraints(inp, result)


def test_assignments_sorted_by_staff_and_date():
    inp = make_input([staff(2), staff(1)])
    result = generate_schedule(inp)
    keys = [(a.staff_id, a.work_date) for a in result.assignments]
    assert keys == sorted(keys)


def test_inactive_staff_never_assigned():
    inp = make_input([staff(1), staff(2, active=False)], conditions=[cond(1, 28)])
    result = generate_schedule(inp)
    assert {a.staff_id for a in result.assignments} == {1}


def test_infeasible_when_not_enough_staff():
    result = generate_schedule(make_input([staff(1), staff(2)], required=3))
    assert result.status == "INFEASIBLE"
    assert result.assignments == []
    assert result.completed_stage == 0
    assert result.objective_overstaff is None
    assert result.objective_prefer_work is None


def test_infeasible_without_active_staff():
    result = generate_schedule(make_input([staff(1, active=False)], conditions=[]))
    assert result.status == "INFEASIBLE"


def test_hc01_weekday_unavailable():
    # 2/1 は日曜(6)
    inp = make_input([staff(1, off_weekdays=(6,)), staff(2)], conditions=[cond(1, 28), cond(2, 28)])
    result = generate_schedule(inp)
    assert_hard_constraints(inp, result)
    assert 1 not in workers_on(result, day(1))


def test_hc02_unavailable_forces_other_staff():
    inp = make_input(
        [staff(1), staff(2)],
        conditions=[cond(1, 28), cond(2, 0)],
        prefs=[PreferenceInput(1, day(10), "UNAVAILABLE")],
    )
    result = generate_schedule(inp)
    assert_hard_constraints(inp, result)
    assert workers_on(result, day(10)) == {2}


def test_hc03_hc04_required_and_zero_days():
    inp = make_input(
        [staff(i) for i in range(1, 5)],
        required=2,
        overrides={day(3): (0, None), day(4): (4, None)},
        conditions=[cond(i, 28) for i in range(1, 5)],
    )
    result = generate_schedule(inp)
    assert_hard_constraints(inp, result)
    assert workers_on(result, day(3)) == set()
    assert workers_on(result, day(4)) == {1, 2, 3, 4}


def test_hc05_max_staff_with_min_minutes():
    # 3名 × 最低18日 = 54 <= 2名 × 28日 = 56
    inp = make_input(
        [staff(i) for i in (1, 2, 3)],
        required=2,
        max_total=2,
        conditions=[cond(i, 18, min_days=18) for i in (1, 2, 3)],
    )
    result = generate_schedule(inp)
    assert_hard_constraints(inp, result)
    assert all(len(workers_on(result, d)) == 2 for d in DATES)


def test_hc05_max_staff_infeasible_not_relaxed():
    # 3名 × 最低19日 = 57 > 56
    inp = make_input(
        [staff(i) for i in (1, 2, 3)],
        required=2,
        max_total=2,
        conditions=[cond(i, 19, min_days=19) for i in (1, 2, 3)],
    )
    assert generate_schedule(inp).status == "INFEASIBLE"


def test_run_solver_applies_settings(monkeypatch):
    captured = {}
    real_solver_cls = cp_model.CpSolver

    class SpySolver(real_solver_cls):
        def solve(self, model, *args, **kwargs):
            captured["max_time"] = self.parameters.max_time_in_seconds
            captured["seed"] = self.parameters.random_seed
            captured["workers"] = self.parameters.num_workers
            captured["presolve"] = self.parameters.cp_model_presolve
            return super().solve(model, *args, **kwargs)

    monkeypatch.setattr(scheduler.cp_model, "CpSolver", SpySolver)
    model = cp_model.CpModel()
    model.new_bool_var("b")
    status, _ = scheduler._run_solver(model, 3.5)
    assert status == "OPTIMAL"
    assert captured == {"max_time": 3.5, "seed": 42, "workers": 1, "presolve": False}


@pytest.mark.parametrize(
    "raw, expected",
    [
        (cp_model.OPTIMAL, "OPTIMAL"),
        (cp_model.FEASIBLE, "FEASIBLE"),
        (cp_model.INFEASIBLE, "INFEASIBLE"),
        (cp_model.UNKNOWN, "UNKNOWN"),
    ],
)
def test_convert_status(raw, expected):
    assert scheduler._convert_status(raw) == expected


def test_convert_status_model_invalid_raises():
    with pytest.raises(RuntimeError):
        scheduler._convert_status(cp_model.MODEL_INVALID)
