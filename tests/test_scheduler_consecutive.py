"""T41 HC09 最大連勤 / T42 HC10 前月連勤."""

import pytest

from scheduler_helpers import (
    assert_hard_constraints,
    cond,
    day,
    make_input,
    max_run,
    staff,
    workers_on,
)
from src.models import LockedAssignmentInput, PreferenceInput
from src.scheduler import generate_schedule


def test_hc09_two_staff_alternate_within_limit():
    inp = make_input(
        [staff(1, max_consec=2), staff(2, max_consec=2)],
        conditions=[cond(1, 14), cond(2, 14)],
    )
    result = generate_schedule(inp)
    assert_hard_constraints(inp, result)
    assert max_run(result, 1) <= 2
    assert max_run(result, 2) <= 2


def test_hc09_single_staff_every_day_is_infeasible():
    inp = make_input([staff(1, max_consec=5)], conditions=[cond(1, 28)])
    assert generate_schedule(inp).status == "INFEASIBLE"


def test_hc09_limit_is_reached_exactly():
    # 目標28日 → 最大連勤3を上限まで使う（月内28日で 3勤1休 → 21日）
    inp = make_input(
        [staff(1, max_consec=3), staff(2)],
        conditions=[cond(1, 28), cond(2, 0)],
    )
    result = generate_schedule(inp)
    assert_hard_constraints(inp, result)
    assert max_run(result, 1) == 3


def test_hc09_window_longer_than_month_has_no_effect():
    inp = make_input([staff(1, max_consec=40)], conditions=[cond(1, 28)])
    result = generate_schedule(inp)
    assert_hard_constraints(inp, result)
    assert max_run(result, 1) == 28


def _carryover_case(max_consec, carryover, lock_days):
    """スタッフ1の月初を固定出勤し、他スタッフで必要人数を賄う."""
    inp = make_input(
        [staff(1, max_consec=max_consec), staff(2)],
        conditions=[cond(1, 0, carryover=carryover), cond(2, 28)],
        locks=[LockedAssignmentInput(1, day(n), True) for n in lock_days],
    )
    return inp, generate_schedule(inp)


@pytest.mark.parametrize(
    "max_consec, carryover, lock_days, feasible",
    [
        (5, 2, [1, 2, 3], True),  # 計画書T42: M5 C2 3連勤 OK
        (5, 2, [1, 2, 3, 4], False),  # 計画書T42: M5 C2 4連勤 NG
        (5, 5, [1], False),  # 計画書T42: M5 C5 1日勤務 NG
        (5, 5, [2, 3, 4, 5, 6], True),  # 1日休めば前月連勤は途切れる
        (5, 0, [1, 2, 3, 4, 5], True),
        (3, 1, [1, 2], True),
        (3, 1, [1, 2, 3], False),
        (40, 10, range(1, 29), True),  # M-C+1 が月より長い: 10 + 28 = 38 <= 40
        (40, 13, range(1, 29), False),  # 13 + 28 = 41 > 40
    ],
)
def test_hc10_carryover(max_consec, carryover, lock_days, feasible):
    inp, result = _carryover_case(max_consec, carryover, lock_days)
    if feasible:
        assert_hard_constraints(inp, result)
    else:
        assert result.status == "INFEASIBLE"


def test_hc10_carryover_without_locks_forces_rest_on_day1():
    # 前月から M 日連勤 → 1日目は必ず休み
    inp = make_input(
        [staff(1, max_consec=4), staff(2)],
        conditions=[cond(1, 28, carryover=4), cond(2, 0)],
    )
    result = generate_schedule(inp)
    assert_hard_constraints(inp, result)
    assert 1 not in workers_on(result, day(1))
    assert max_run(result, 1, carryover=4) <= 4


def test_hc10_carryover_only_affects_month_start():
    # C=2, M=3: 月初は1日しか続けられないが、月中は3連勤可能
    inp = make_input(
        [staff(1, max_consec=3), staff(2)],
        conditions=[cond(1, 28, carryover=2), cond(2, 0)],
    )
    result = generate_schedule(inp)
    assert_hard_constraints(inp, result)
    assert max_run(result, 1, carryover=2) == 3


def test_hc10_carryover_greater_than_max_is_infeasible_not_relaxed():
    inp = make_input(
        [staff(1, max_consec=3), staff(2)],
        conditions=[cond(1, 0, carryover=4), cond(2, 28)],
    )
    assert generate_schedule(inp).status == "INFEASIBLE"


def test_hc10_forced_by_staffing():
    # 1〜2日目は staff1 しか出勤できない。前月2連勤 + 2 = 4 > 3 → 不成立
    prefs = [PreferenceInput(2, day(1), "UNAVAILABLE"), PreferenceInput(2, day(2), "UNAVAILABLE")]
    infeasible = make_input(
        [staff(1, max_consec=3), staff(2)],
        conditions=[cond(1, 10, carryover=2), cond(2, 10)],
        prefs=prefs,
    )
    assert generate_schedule(infeasible).status == "INFEASIBLE"

    feasible = make_input(
        [staff(1, max_consec=3), staff(2)],
        conditions=[cond(1, 10, carryover=1), cond(2, 10)],
        prefs=prefs,
    )
    result = generate_schedule(feasible)
    assert_hard_constraints(feasible, result)
