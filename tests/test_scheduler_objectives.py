"""T48〜T56: 段階最適化・時間配分・hint・UNKNOWNフォールバック.

v1.4 の優先順位: 1 目標日数との差の合計 → 2 差の最大値 → 3 日別超過人数の最大値
→ 4 PREFER_OFF → 5 PREFER_WORK。最低人数（required_total_staff）はHard下限。
"""

import time
import types

from scheduler_helpers import (
    DATES,
    assert_hard_constraints,
    cond,
    day,
    make_input,
    staff,
    workdays,
    workers_on,
)
from src import scheduler
from src.models import (
    DailyRequirementInput,
    MonthlyConditionInput,
    PreferenceInput,
    RoleRequirementInput,
    SchedulerInput,
    StaffInput,
)
from src.month_utils import get_month_dates, weekday_index
from src.scheduler import build_model, generate_schedule, solve_lexicographically

# ---------------------------------------------------------------------------
# 各Stageの目的と優先順位
# ---------------------------------------------------------------------------


def test_stage1_target_days_take_priority_over_minimum_staff():
    # 最低人数1だが全員28日勤務が目標 → 最低人数を超えて全員出勤させる（v1.4）
    inp = make_input([staff(1), staff(2), staff(3)], conditions=[cond(i, 28) for i in (1, 2, 3)])
    result = generate_schedule(inp)
    assert_hard_constraints(inp, result)
    assert result.objective_target_deviation == 0
    assert all(len(workers_on(result, d)) == 3 for d in DATES)
    assert result.objective_overstaff == 2 * 28
    assert result.objective_max_overstaff == 2


def test_max_staff_still_caps_extra_staff():
    # 最大人数2（Hard）→ 目標28日でも1日2名まで
    inp = make_input(
        [staff(1), staff(2), staff(3)],
        max_total=2,
        conditions=[cond(i, 28) for i in (1, 2, 3)],
    )
    result = generate_schedule(inp)
    assert_hard_constraints(inp, result)
    assert all(len(workers_on(result, d)) == 2 for d in DATES)
    assert result.objective_target_deviation == 3 * 28 - 2 * 28


def test_stage2_spreads_shortfall_evenly():
    # 1日ちょうど1名（最低1・最大1）で延べ28日、目標は3名×20日=60日 → 不足32日を均等に
    inp = make_input(
        [staff(1), staff(2), staff(3)],
        max_total=1,
        conditions=[cond(i, 20) for i in (1, 2, 3)],
    )
    result = generate_schedule(inp)
    assert_hard_constraints(inp, result)
    shortfalls = sorted(20 - len(workdays(result, i)) for i in (1, 2, 3))
    assert sum(shortfalls) == 32
    assert result.objective_target_deviation == 32
    assert result.objective_max_deviation == 11
    assert shortfalls == [10, 11, 11]


def test_stage2_spreads_excess_evenly_when_minimum_exceeds_targets():
    # 最低人数2で延べ56日。staff3 は28日目標、staff1/2 は10日目標 → 残り28日を均等に14日ずつ
    inp = make_input(
        [staff(1), staff(2), staff(3)],
        required=2,
        conditions=[cond(1, 10), cond(2, 10), cond(3, 28)],
    )
    result = generate_schedule(inp)
    assert_hard_constraints(inp, result)
    assert len(workdays(result, 3)) == 28
    assert sorted(len(workdays(result, i)) for i in (1, 2)) == [14, 14]
    assert result.objective_max_deviation == 4


def test_stage3_levels_extra_staff_across_days():
    # 最低1名×28日=28、目標 4名×14日=56 → 余剰28人日を1日1名ずつに平準化
    inp = make_input([staff(i) for i in (1, 2, 3, 4)], conditions=[cond(i, 14) for i in (1, 2, 3, 4)])
    result = generate_schedule(inp)
    assert_hard_constraints(inp, result)
    assert result.objective_target_deviation == 0
    assert result.objective_max_overstaff == 1
    assert all(len(workers_on(result, d)) == 2 for d in DATES)
    assert result.objective_overstaff == 28


def test_stage1_overstaff_counts_forced_extra_staff():
    # 最低勤務日数で過剰配置が避けられない: 2名 × 最低20日 = 40 > 28
    inp = make_input(
        [staff(1), staff(2)],
        conditions=[cond(1, 20, min_days=20), cond(2, 20, min_days=20)],
    )
    result = generate_schedule(inp)
    assert_hard_constraints(inp, result)
    assert result.objective_overstaff == 12


def test_stage2_target_workdays_met():
    inp = make_input([staff(1), staff(2)], conditions=[cond(1, 14), cond(2, 14)])
    result = generate_schedule(inp)
    assert result.objective_target_deviation == 0
    assert len(workdays(result, 1)) == 14
    assert len(workdays(result, 2)) == 14


def test_stage2_uses_round_half_up_target():
    # 14.5日 → 15日（四捨五入）, 残り13日
    inp = make_input(
        [staff(1), staff(2)],
        conditions=[cond(1, target=14 * 480 + 240), cond(2, 13)],
    )
    result = generate_schedule(inp)
    assert result.objective_target_deviation == 0
    assert len(workdays(result, 1)) == 15
    assert len(workdays(result, 2)) == 13


def test_stage2_takes_priority_over_stage3():
    # staff1 は全日勤務が目標 → PREFER_OFF より目標日数を優先
    inp = make_input(
        [staff(1), staff(2)],
        conditions=[cond(1, 28), cond(2, 0)],
        prefs=[PreferenceInput(1, day(5), "PREFER_OFF")],
    )
    result = generate_schedule(inp)
    assert result.objective_target_deviation == 0
    assert result.objective_prefer_off == 1
    assert 1 in workers_on(result, day(5))


def test_stage3_prefer_off_respected_when_free():
    inp = make_input(
        [staff(1), staff(2)],
        conditions=[cond(1, 14), cond(2, 14)],
        prefs=[PreferenceInput(1, day(d), "PREFER_OFF") for d in range(1, 8)],
    )
    result = generate_schedule(inp)
    assert result.objective_target_deviation == 0
    assert result.objective_prefer_off == 0
    assert not set(workdays(result, 1)) & {day(d) for d in range(1, 8)}


def test_stage3_takes_priority_over_stage4():
    # 5日・6日のみ必要人数1（最大1）。staff1 は連勤上限1。
    #   (1が5日, 2が6日): PREFER_OFF違反1 / (2が5日, 1が6日): PREFER_WORK未反映1
    # → Stage 3 を優先し PREFER_OFF 違反0
    overrides = {d: (0, None) for d in DATES}
    overrides[day(5)] = (1, 1)
    overrides[day(6)] = (1, 1)
    inp = make_input(
        [staff(1, max_consec=1), staff(2)],
        overrides=overrides,
        conditions=[cond(1, 1), cond(2, 1)],
        prefs=[
            PreferenceInput(1, day(5), "PREFER_WORK"),
            PreferenceInput(2, day(6), "PREFER_OFF"),
        ],
    )
    result = generate_schedule(inp)
    assert_hard_constraints(inp, result)
    assert result.objective_prefer_off == 0
    assert result.objective_prefer_work == 1
    assert workers_on(result, day(5)) == {2}
    assert workers_on(result, day(6)) == {1}


def test_stage4_prefer_work_respected_when_free():
    inp = make_input(
        [staff(1), staff(2)],
        conditions=[cond(1, 14), cond(2, 14)],
        prefs=[PreferenceInput(2, day(d), "PREFER_WORK") for d in range(1, 8)],
    )
    result = generate_schedule(inp)
    assert result.objective_prefer_work == 0
    assert {day(d) for d in range(1, 8)} <= set(workdays(result, 2))


def test_no_preferences_gives_zero_objectives():
    result = generate_schedule(make_input([staff(1), staff(2)], conditions=[cond(1, 14), cond(2, 14)]))
    assert result.status == "OPTIMAL"
    assert result.objective_prefer_off == 0
    assert result.objective_prefer_work == 0


# ---------------------------------------------------------------------------
# Solver制御: 時間配分・hint・フォールバック（§38）
# ---------------------------------------------------------------------------


def _simple_input():
    return make_input(
        [staff(1), staff(2), staff(3)],
        conditions=[cond(1, 10), cond(2, 10), cond(3, 8)],
        prefs=[PreferenceInput(1, day(3), "PREFER_OFF"), PreferenceInput(2, day(4), "PREFER_WORK")],
    )


def _patch_solver(monkeypatch, status_by_stage):
    """指定Stageの戻りステータスだけ差し替える（それ以外は実Solver）."""
    calls = []
    real = scheduler._run_solver

    def fake(model, remaining):
        stage = len(calls) + 1
        calls.append({"remaining": remaining, "hints": len(model.proto.solution_hint.vars)})
        status, solver = real(model, remaining)
        override = status_by_stage.get(stage)
        if override is None:
            return status, solver
        return override, solver

    monkeypatch.setattr(scheduler, "_run_solver", fake)
    return calls


def test_all_stages_optimal_records_stage_results():
    result = generate_schedule(_simple_input())
    assert result.status == "OPTIMAL"
    assert result.completed_stage == 5
    assert [(r.stage, r.solver_status) for r in result.stage_results] == [
        (1, "OPTIMAL"),
        (2, "OPTIMAL"),
        (3, "OPTIMAL"),
        (4, "OPTIMAL"),
        (5, "OPTIMAL"),
    ]
    assert [r.objective_value for r in result.stage_results] == [
        result.objective_target_deviation,
        result.objective_max_deviation,
        result.objective_max_overstaff,
        result.objective_prefer_off,
        result.objective_prefer_work,
    ]


def test_hints_passed_from_stage2(monkeypatch):
    calls = _patch_solver(monkeypatch, {})
    inp = _simple_input()
    generate_schedule(inp)
    n_vars = 3 * len(DATES)
    assert [c["hints"] for c in calls] == [0, n_vars, n_vars, n_vars, n_vars]


def test_remaining_time_decreases_and_starts_at_total(monkeypatch):
    calls = _patch_solver(monkeypatch, {})
    generate_schedule(_simple_input())
    remaining = [c["remaining"] for c in calls]
    assert remaining[0] <= 10.0
    assert remaining[0] > 9.0
    assert remaining == sorted(remaining, reverse=True)


def test_stage1_unknown_returns_no_assignments(monkeypatch):
    _patch_solver(monkeypatch, {1: "UNKNOWN"})
    result = generate_schedule(_simple_input())
    assert result.status == "UNKNOWN"
    assert result.assignments == []
    assert result.completed_stage == 0
    assert result.objective_overstaff is None


def test_stage3_unknown_falls_back_to_stage2_solution(monkeypatch):
    calls = _patch_solver(monkeypatch, {3: "UNKNOWN"})
    inp = _simple_input()
    result = generate_schedule(inp)
    assert len(calls) == 3  # Stage 4 以降は実行しない
    assert result.status == "FEASIBLE"
    assert result.completed_stage == 2
    assert result.objective_overstaff is not None  # 解の実績値は常に出す
    assert result.objective_target_deviation is not None
    assert result.objective_max_deviation is not None
    assert result.objective_max_overstaff is None
    assert result.objective_prefer_off is None
    assert result.objective_prefer_work is None
    assert [(r.stage, r.solver_status) for r in result.stage_results] == [
        (1, "OPTIMAL"),
        (2, "OPTIMAL"),
        (3, "UNKNOWN"),
    ]
    assert_hard_constraints(inp, result)


def test_stage2_unknown_falls_back_to_stage1_solution(monkeypatch):
    _patch_solver(monkeypatch, {2: "UNKNOWN"})
    inp = _simple_input()
    result = generate_schedule(inp)
    assert result.status == "FEASIBLE"
    assert result.completed_stage == 1
    assert result.objective_target_deviation is not None
    assert result.objective_max_deviation is None
    assert_hard_constraints(inp, result)


def test_feasible_stage_counts_as_completed_but_not_optimal(monkeypatch):
    _patch_solver(monkeypatch, {2: "FEASIBLE"})
    result = generate_schedule(_simple_input())
    assert result.status == "FEASIBLE"
    assert result.completed_stage == 5
    assert result.stage_results[1].solver_status == "FEASIBLE"
    assert result.objective_prefer_work is not None


def test_stage3_infeasible_falls_back_to_stage2_solution(monkeypatch):
    _patch_solver(monkeypatch, {3: "INFEASIBLE"})
    inp = _simple_input()
    result = generate_schedule(inp)
    assert result.status == "FEASIBLE"
    assert result.completed_stage == 2
    assert (result.stage_results[-1].stage, result.stage_results[-1].solver_status) == (3, "INFEASIBLE")
    assert_hard_constraints(inp, result)


def test_stage5_unknown_falls_back_to_stage4_solution(monkeypatch):
    _patch_solver(monkeypatch, {5: "UNKNOWN"})
    result = generate_schedule(_simple_input())
    assert result.status == "FEASIBLE"
    assert result.completed_stage == 4
    assert result.objective_prefer_off is not None
    assert result.objective_prefer_work is None


def test_hint_values_equal_previous_stage_solution(monkeypatch):
    real = scheduler._run_solver
    captured = []

    def spy(model, remaining):
        hint = dict(zip(model.proto.solution_hint.vars, model.proto.solution_hint.values))
        status, solver = real(model, remaining)
        captured.append((hint, solver))
        return status, solver

    monkeypatch.setattr(scheduler, "_run_solver", spy)
    sm = build_model(_simple_input())
    solve_lexicographically(sm)
    for (_, prev_solver), (hint, _) in zip(captured, captured[1:]):
        expected = {v.index: int(prev_solver.boolean_value(v)) for v in sm.x.values()}
        assert hint == expected


def test_time_exhausted_before_stage1_is_unknown():
    result = solve_lexicographically(build_model(_simple_input()), time_limit_seconds=0)
    assert result.status == "UNKNOWN"
    assert result.assignments == []
    assert result.completed_stage == 0
    assert result.stage_results == []


def test_time_exhausted_after_stage1_returns_stage1_solution(monkeypatch):
    # Stage 1 実行後に時計を進め、残り時間0にする
    clock = {"now": 0.0}
    monkeypatch.setattr(scheduler, "time", types.SimpleNamespace(monotonic=lambda: clock["now"]))
    real = scheduler._run_solver

    def advancing(model, remaining):
        result = real(model, remaining)
        clock["now"] += 100.0
        return result

    monkeypatch.setattr(scheduler, "_run_solver", advancing)
    inp = _simple_input()
    result = generate_schedule(inp)
    assert result.status == "FEASIBLE"
    assert result.completed_stage == 1
    assert result.objective_overstaff is not None
    assert result.objective_target_deviation is not None
    assert result.objective_max_deviation is None
    assert [r.stage for r in result.stage_results] == [1]
    assert_hard_constraints(inp, result)


def test_objective_equality_constraint_keeps_previous_stage_value():
    # 後段(PREFER_WORK)が全日出勤を望んでも Stage 1 の目標差0は維持される（§36）
    inp = make_input(
        [staff(1), staff(2)],
        conditions=[cond(1, 14), cond(2, 14)],
        prefs=[PreferenceInput(i, day(d), "PREFER_WORK") for i in (1, 2) for d in range(1, 29)],
    )
    result = generate_schedule(inp)
    assert result.objective_target_deviation == 0
    assert len(workdays(result, 1)) == 14 and len(workdays(result, 2)) == 14
    assert result.objective_prefer_work == 2 * 28 - 28


# ---------------------------------------------------------------------------
# 現実規模: 15名 × 31日で10秒以内（§56）
# ---------------------------------------------------------------------------


def _demo_input() -> SchedulerInput:
    ym = "2026-10"
    dates = get_month_dates(ym)
    roles = [1] * 3 + [2] * 3 + [3] * 9
    minutes = [480, 360, 300]
    staff_list = [
        StaffInput(
            staff_id=i + 1,
            staff_name=f"S{i + 1}",
            role_id=roles[i],
            daily_work_minutes=minutes[i % 3],
            max_consecutive_days=5,
            weekday_availability={w: not (i % 4 == 0 and w == 6) for w in range(7)},
        )
        for i in range(15)
    ]
    conditions = [
        MonthlyConditionInput(
            s.staff_id,
            ym,
            s.daily_work_minutes * (20 - s.staff_id % 3 * 4),
            s.daily_work_minutes * 8,
            s.daily_work_minutes * 23,
            s.staff_id % 4,
        )
        for s in staff_list
    ]
    reqs = [DailyRequirementInput(d, 9 if weekday_index(d) >= 5 else 7, 11) for d in dates]
    role_reqs = [RoleRequirementInput(d, r, 1) for d in dates for r in (1, 2)]
    prefs = []
    for s in staff_list:
        prefs.append(PreferenceInput(s.staff_id, dates[s.staff_id], "UNAVAILABLE"))
        prefs.append(PreferenceInput(s.staff_id, dates[s.staff_id + 7], "PREFER_OFF"))
        work_day = dates[s.staff_id + 14]
        if s.is_available_on(weekday_index(work_day)):
            prefs.append(PreferenceInput(s.staff_id, work_day, "PREFER_WORK"))
    return SchedulerInput(ym, staff_list, conditions, reqs, role_reqs, prefs, [])


def test_realistic_month_within_time_limit():
    inp = _demo_input()
    started = time.monotonic()
    result = generate_schedule(inp)
    elapsed = time.monotonic() - started
    assert result.has_solution
    assert elapsed < 10.5
    # UNAVAILABLE は100%反映
    g = {(a.staff_id, a.work_date): a.is_working for a in result.assignments}
    for p in inp.preferences:
        if p.preference_type == "UNAVAILABLE":
            assert g[p.staff_id, p.work_date] is False


def test_target_longer_than_month_is_still_solvable():
    # レビュー指摘: 目標日数 > 月の日数でも差の最大値の上限で不成立にしない
    # 日曜のみ勤務可・目標40日（28日月） → 4日しか働けないが有効なシフトは作れる
    inp = make_input(
        [staff(1, off_weekdays=(0, 1, 2, 3, 4, 5), minutes=240), staff(2)],
        conditions=[cond(1, 40, minutes=240), cond(2, 28)],
    )
    result = generate_schedule(inp)
    assert result.status == "OPTIMAL"
    assert_hard_constraints(inp, result)
    assert len(workdays(result, 1)) == 4
    assert result.objective_max_deviation == 36
