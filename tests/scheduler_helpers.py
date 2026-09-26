"""Solverテスト共通の入力ビルダーと検証ヘルパー（§39: セル完全一致は要求しない）."""

from src.models import (
    DailyRequirementInput,
    MonthlyConditionInput,
    SchedulerInput,
    SchedulerResult,
    StaffInput,
)
from src.month_utils import get_month_dates, round_half_up_workdays, weekday_index

YM = "2026-02"  # 28日。2026-02-01 は日曜(6)
DATES = get_month_dates(YM)
LEADER, CHECKER, CLEANER = 1, 2, 3


def day(n: int) -> str:
    return DATES[n - 1]


def staff(staff_id, role_id=CLEANER, *, active=True, max_consec=31, minutes=480, off_weekdays=()):
    return StaffInput(
        staff_id=staff_id,
        staff_name=f"S{staff_id}",
        role_id=role_id,
        daily_work_minutes=minutes,
        max_consecutive_days=max_consec,
        active=active,
        weekday_availability={w: w not in off_weekdays for w in range(7)},
    )


def cond(staff_id, target_days=0, *, minutes=480, min_days=None, max_days=None, carryover=0, target=None):
    return MonthlyConditionInput(
        staff_id=staff_id,
        year_month=YM,
        target_monthly_minutes=target if target is not None else target_days * minutes,
        min_monthly_minutes=None if min_days is None else min_days * minutes,
        max_monthly_minutes=None if max_days is None else max_days * minutes,
        carryover_consecutive_days=carryover,
    )


def make_input(
    staff_list,
    *,
    required=1,
    max_total=None,
    overrides=None,
    conditions=None,
    role_reqs=(),
    prefs=(),
    locks=(),
):
    overrides = overrides or {}
    reqs = [
        DailyRequirementInput(d, *overrides.get(d, (required, max_total))) for d in DATES
    ]
    if conditions is None:
        conditions = [cond(s.staff_id) for s in staff_list]
    return SchedulerInput(
        year_month=YM,
        staff=list(staff_list),
        monthly_conditions=list(conditions),
        daily_requirements=reqs,
        role_requirements=list(role_reqs),
        preferences=list(prefs),
        locked_assignments=list(locks),
    )


def grid(result: SchedulerResult) -> dict[tuple[int, str], bool]:
    return {(a.staff_id, a.work_date): a.is_working for a in result.assignments}


def workers_on(result: SchedulerResult, work_date: str) -> set[int]:
    return {a.staff_id for a in result.assignments if a.work_date == work_date and a.is_working}


def workdays(result: SchedulerResult, staff_id: int) -> list[str]:
    return [a.work_date for a in result.assignments if a.staff_id == staff_id and a.is_working]


def max_run(result: SchedulerResult, staff_id: int, carryover: int = 0) -> int:
    worked = set(workdays(result, staff_id))
    best = run = carryover
    for d in DATES:
        run = run + 1 if d in worked else 0
        best = max(best, run)
    return best


def assert_hard_constraints(inp: SchedulerInput, result: SchedulerResult) -> None:
    """全Hard Constraint（HC01〜HC12）を満たすことを検証する."""
    assert result.has_solution, result.status
    active = [s for s in inp.staff if s.active]
    g = grid(result)
    assert set(g) == {(s.staff_id, d) for s in active for d in DATES}

    conditions = {c.staff_id: c for c in inp.monthly_conditions}
    prefs = {(p.staff_id, p.work_date): p.preference_type for p in inp.preferences}
    reqs = {r.work_date: r for r in inp.daily_requirements}

    for s in active:
        for d in DATES:
            if not s.is_available_on(weekday_index(d)):
                assert not g[s.staff_id, d], ("HC01", s.staff_id, d)
            if prefs.get((s.staff_id, d)) == "UNAVAILABLE":
                assert not g[s.staff_id, d], ("HC02", s.staff_id, d)
        c = conditions.get(s.staff_id)
        minutes = len(workdays(result, s.staff_id)) * s.daily_work_minutes
        carry = 0
        if c is not None:
            if c.max_monthly_minutes is not None:
                assert minutes <= c.max_monthly_minutes, ("HC07", s.staff_id)
            if c.min_monthly_minutes is not None:
                assert minutes >= c.min_monthly_minutes, ("HC08", s.staff_id)
            carry = c.carryover_consecutive_days
        assert max_run(result, s.staff_id, carry) <= s.max_consecutive_days, ("HC09/10", s.staff_id)

    for d in DATES:
        total = sum(g[s.staff_id, d] for s in active)
        req = reqs[d]
        assert total >= req.required_total_staff, ("HC03", d)
        if req.required_total_staff == 0:
            assert total == 0, ("HC04", d)
        if req.max_total_staff is not None:
            assert total <= req.max_total_staff, ("HC05", d)

    for rr in inp.role_requirements:
        count = sum(g[s.staff_id, rr.work_date] for s in active if s.role_id == rr.role_id)
        assert count >= rr.required_count, ("HC06", rr.work_date, rr.role_id)

    active_ids = {s.staff_id for s in active}
    for lock in inp.locked_assignments:
        if lock.staff_id in active_ids and lock.work_date in DATES:
            assert g[lock.staff_id, lock.work_date] == lock.is_working, ("HC11/12", lock)


def target_workdays(c: MonthlyConditionInput, daily_work_minutes: int) -> int:
    return round_half_up_workdays(c.target_monthly_minutes, daily_work_minutes)
