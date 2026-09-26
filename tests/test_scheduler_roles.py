"""T37 HC06: ロール別必要人数（role_idベース）."""

from scheduler_helpers import (
    CHECKER,
    CLEANER,
    DATES,
    LEADER,
    assert_hard_constraints,
    cond,
    day,
    make_input,
    staff,
    workers_on,
)
from src.models import PreferenceInput, RoleRequirementInput
from src.scheduler import generate_schedule


def _team():
    return [
        staff(1, LEADER),
        staff(2, LEADER),
        staff(3, CHECKER),
        staff(4, CLEANER),
        staff(5, CLEANER),
    ]


def test_role_requirement_every_day():
    team = _team()
    inp = make_input(
        team,
        required=2,
        conditions=[cond(s.staff_id, 12) for s in team],
        role_reqs=[RoleRequirementInput(d, LEADER, 1) for d in DATES]
        + [RoleRequirementInput(d, CHECKER, 1) for d in DATES],
    )
    result = generate_schedule(inp)
    assert_hard_constraints(inp, result)
    for d in DATES:
        on = workers_on(result, d)
        assert on & {1, 2}
        assert 3 in on


def test_role_requirement_forces_specific_staff():
    team = _team()
    inp = make_input(
        team,
        conditions=[cond(s.staff_id, 5) for s in team],
        role_reqs=[RoleRequirementInput(day(10), LEADER, 1)],
        prefs=[PreferenceInput(1, day(10), "UNAVAILABLE")],
    )
    result = generate_schedule(inp)
    assert_hard_constraints(inp, result)
    assert 2 in workers_on(result, day(10))


def test_role_shortage_is_infeasible():
    team = _team()
    inp = make_input(
        team,
        conditions=[cond(s.staff_id, 5) for s in team],
        role_reqs=[RoleRequirementInput(day(10), LEADER, 1)],
        prefs=[
            PreferenceInput(1, day(10), "UNAVAILABLE"),
            PreferenceInput(2, day(10), "UNAVAILABLE"),
        ],
    )
    assert generate_schedule(inp).status == "INFEASIBLE"


def test_role_requirement_zero_without_staff_of_role():
    team = [staff(4, CLEANER), staff(5, CLEANER)]
    inp = make_input(team, role_reqs=[RoleRequirementInput(d, LEADER, 0) for d in DATES])
    assert generate_schedule(inp).has_solution


def test_role_requirement_counts_toward_total():
    # 必要人数2、LEADER 2 → LEADER 2名で充足し、過剰配置なし
    team = _team()
    inp = make_input(
        team,
        required=2,
        conditions=[cond(s.staff_id, 0) for s in team],
        role_reqs=[RoleRequirementInput(d, LEADER, 2) for d in DATES],
    )
    result = generate_schedule(inp)
    assert_hard_constraints(inp, result)
    assert result.objective_overstaff == 0
    assert all(workers_on(result, d) == {1, 2} for d in DATES)
