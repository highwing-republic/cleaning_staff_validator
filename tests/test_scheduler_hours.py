"""T38〜T40 HC07/HC08: 月間勤務時間（分）の上限・下限."""

from scheduler_helpers import (
    YM,
    assert_hard_constraints,
    cond,
    make_input,
    staff,
    workdays,
)
from src.models import MonthlyConditionInput
from src.scheduler import build_model, generate_schedule


def test_actual_minutes_expression_uses_daily_minutes():
    inp = make_input([staff(1, minutes=360)], conditions=[cond(1, 0, minutes=360)])
    sm = build_model(inp)
    expr = sm.actual_minutes(sm.staff[0])
    # 係数が daily_work_minutes であること（式作成のみ, T38）
    assert "360" in str(expr)


def test_max_minutes_caps_workdays():
    # 目標は28日だが最大10日 → Hard が優先
    inp = make_input(
        [staff(1), staff(2)],
        conditions=[cond(1, 28, max_days=10), cond(2, 18)],
    )
    result = generate_schedule(inp)
    assert_hard_constraints(inp, result)
    assert len(workdays(result, 1)) == 10


def test_max_minutes_boundary_with_non_divisible_limit():
    # 最大 4799分 / 480分 → 9日まで
    inp = make_input(
        [staff(1), staff(2)],
        conditions=[MonthlyConditionInput(1, YM, 4799, None, 4799), cond(2, 19)],
    )
    result = generate_schedule(inp)
    assert_hard_constraints(inp, result)
    assert len(workdays(result, 1)) == 9


def test_min_minutes_forces_workdays():
    # 目標0日でも最低12日
    inp = make_input(
        [staff(1), staff(2)],
        conditions=[cond(1, 0, min_days=12), cond(2, 28)],
    )
    result = generate_schedule(inp)
    assert_hard_constraints(inp, result)
    assert len(workdays(result, 1)) == 12


def test_min_minutes_with_short_daily_minutes():
    # 6h勤務・最低 3000分 → 9日（8日=2880 < 3000）
    inp = make_input(
        [staff(1, minutes=360), staff(2)],
        conditions=[MonthlyConditionInput(1, YM, 0, 3000, None), cond(2, 28)],
    )
    result = generate_schedule(inp)
    assert_hard_constraints(inp, result)
    assert len(workdays(result, 1)) == 9


def test_min_greater_than_possible_is_infeasible():
    inp = make_input([staff(1)], conditions=[cond(1, 28, min_days=29)])
    assert generate_schedule(inp).status == "INFEASIBLE"


def test_none_min_max_means_no_constraint():
    inp = make_input([staff(1), staff(2)], conditions=[cond(1, 28), cond(2, 0)])
    result = generate_schedule(inp)
    assert_hard_constraints(inp, result)
    assert len(workdays(result, 1)) == 28
