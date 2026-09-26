from src.models import (
    AssignmentResult,
    DailyRequirementInput,
    MonthlyConditionInput,
    PreferenceInput,
    RoleRequirementInput,
    SchedulerInput,
    StaffInput,
)
from src.month_utils import get_month_dates
from src.schedule_validation import (
    check_consecutive,
    check_hours,
    check_max_staff,
    check_roles,
    check_staffing,
    check_unavailable,
    check_weekday,
    summarize_staff_minutes,
    validate_schedule,
)

YM = "2026-02"  # 28日。2026-02-01 は日曜(6)、2026-02-02 は月曜(0)
DATES = get_month_dates(YM)
LEADER, CHECKER, CLEANER = 1, 2, 3


def staff(staff_id, role_id=CLEANER, *, active=True, max_consec=5, minutes=480, off_weekdays=()):
    return StaffInput(
        staff_id=staff_id,
        staff_name=f"S{staff_id}",
        role_id=role_id,
        daily_work_minutes=minutes,
        max_consecutive_days=max_consec,
        active=active,
        weekday_availability={w: w not in off_weekdays for w in range(7)},
    )


def cond(staff_id, target=4800, *, min_=None, max_=None, carryover=0):
    return MonthlyConditionInput(staff_id, YM, target, min_, max_, carryover)


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
    missing_dates=(),
):
    overrides = overrides or {}
    reqs = []
    for d in DATES:
        if d in missing_dates:
            continue
        req, mx = overrides.get(d, (required, max_total))
        reqs.append(DailyRequirementInput(d, req, mx))
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


def work(staff_id, work_date, is_working=True):
    return AssignmentResult(staff_id, work_date, is_working)


def codes(errors):
    return [e.code for e in errors]


# ---------------------------------------------------------------------------
# validate_schedule composition
# ---------------------------------------------------------------------------


def test_fully_valid_schedule_returns_empty():
    staff_list = [staff(1), staff(2)]
    scheduler_input = make_input(
        staff_list, required=0, overrides={DATES[0]: (1, None)}
    )
    assignments = [work(1, DATES[0])]
    assert validate_schedule(scheduler_input, assignments) == []


def test_multiple_violations_are_collected():
    s1 = staff(1, off_weekdays=(0, 1, 2, 3, 4, 5, 6))  # never available
    scheduler_input = make_input([s1], required=1)
    assignments = [work(1, DATES[0])]  # HC01 (weekday) and HC03 (still short of required)
    errors = validate_schedule(scheduler_input, assignments)
    assert "HC01" in codes(errors)
    assert "HC03" in codes(errors)


# ---------------------------------------------------------------------------
# HC01 weekday
# ---------------------------------------------------------------------------


def test_hc01_violated_when_working_on_unavailable_weekday():
    s1 = staff(1, off_weekdays=(weekday_of(DATES[0]),))
    scheduler_input = make_input([s1], required=0)
    errors = check_weekday(scheduler_input, [work(1, DATES[0])])
    assert codes(errors) == ["HC01"]
    assert errors[0].staff_id == 1
    assert errors[0].work_date == DATES[0]


def test_hc01_satisfied_when_available_weekday():
    s1 = staff(1)
    scheduler_input = make_input([s1], required=0)
    errors = check_weekday(scheduler_input, [work(1, DATES[0])])
    assert errors == []


def weekday_of(work_date):
    from src.month_utils import weekday_index

    return weekday_index(work_date)


# ---------------------------------------------------------------------------
# HC02 UNAVAILABLE
# ---------------------------------------------------------------------------


def test_hc02_violated_when_working_on_unavailable_preference_day():
    s1 = staff(1)
    prefs = [PreferenceInput(1, DATES[0], "UNAVAILABLE")]
    scheduler_input = make_input([s1], required=0, prefs=prefs)
    errors = check_unavailable(scheduler_input, [work(1, DATES[0])])
    assert codes(errors) == ["HC02"]
    assert errors[0].staff_id == 1
    assert errors[0].work_date == DATES[0]


def test_hc02_satisfied_when_off_on_unavailable_day():
    s1 = staff(1)
    prefs = [PreferenceInput(1, DATES[0], "UNAVAILABLE")]
    scheduler_input = make_input([s1], required=0, prefs=prefs)
    errors = check_unavailable(scheduler_input, [work(1, DATES[0], False)])
    assert errors == []


# ---------------------------------------------------------------------------
# HC03 / HC04 staffing
# ---------------------------------------------------------------------------


def test_hc03_violated_below_required():
    staff_list = [staff(1), staff(2), staff(3)]
    scheduler_input = make_input(
        staff_list, required=0, overrides={DATES[0]: (3, None)}
    )
    assignments = [work(1, DATES[0]), work(2, DATES[0])]  # 2 < 3
    errors = check_staffing(scheduler_input, assignments)
    assert codes(errors) == ["HC03"]
    assert "2" in errors[0].message
    assert "1名不足" in errors[0].message


def test_hc03_satisfied_when_exactly_required():
    staff_list = [staff(1), staff(2), staff(3)]
    scheduler_input = make_input(
        staff_list, required=0, overrides={DATES[0]: (2, None)}
    )
    assignments = [work(1, DATES[0]), work(2, DATES[0])]
    errors = check_staffing(scheduler_input, assignments)
    assert errors == []


def test_hc04_violated_when_required_zero_but_someone_works():
    staff_list = [staff(1)]
    scheduler_input = make_input(staff_list, required=0)
    errors = check_staffing(scheduler_input, [work(1, DATES[0])])
    assert codes(errors) == ["HC04"]
    assert errors[0].work_date == DATES[0]


def test_hc04_satisfied_when_required_zero_and_nobody_works():
    staff_list = [staff(1)]
    scheduler_input = make_input(staff_list, required=0)
    errors = check_staffing(scheduler_input, [])
    assert errors == []


def test_missing_daily_requirement_skips_staffing_check():
    staff_list = [staff(1)]
    scheduler_input = make_input(staff_list, required=1, missing_dates=(DATES[0],))
    # nobody works on DATES[0], which would normally be fine since required=0
    # is not set; but requirement row is missing entirely -> must be skipped
    errors = check_staffing(scheduler_input, [])
    assert all(e.work_date != DATES[0] for e in errors)


# ---------------------------------------------------------------------------
# HC05 max staff
# ---------------------------------------------------------------------------


def test_hc05_violated_above_max():
    staff_list = [staff(1), staff(2), staff(3)]
    scheduler_input = make_input(staff_list, required=0, max_total=2)
    assignments = [work(1, DATES[0]), work(2, DATES[0]), work(3, DATES[0])]
    errors = check_max_staff(scheduler_input, assignments)
    assert codes(errors) == ["HC05"]
    assert "1名超過" in errors[0].message


def test_hc05_satisfied_when_exactly_max():
    staff_list = [staff(1), staff(2)]
    scheduler_input = make_input(staff_list, required=0, max_total=2)
    assignments = [work(1, DATES[0]), work(2, DATES[0])]
    errors = check_max_staff(scheduler_input, assignments)
    assert errors == []


def test_hc05_no_check_when_max_total_staff_is_none():
    staff_list = [staff(1), staff(2)]
    scheduler_input = make_input(staff_list, required=0, max_total=None)
    assignments = [work(1, DATES[0]), work(2, DATES[0])]
    errors = check_max_staff(scheduler_input, assignments)
    assert errors == []


def test_missing_daily_requirement_skips_max_staff_check():
    staff_list = [staff(1), staff(2)]
    scheduler_input = make_input(
        staff_list, required=0, max_total=1, missing_dates=(DATES[0],)
    )
    assignments = [work(1, DATES[0]), work(2, DATES[0])]
    errors = check_max_staff(scheduler_input, assignments)
    assert errors == []


# ---------------------------------------------------------------------------
# HC06 role
# ---------------------------------------------------------------------------


def test_hc06_violated_role_shortage():
    staff_list = [staff(1, role_id=LEADER), staff(2, role_id=CLEANER)]
    role_reqs = [RoleRequirementInput(DATES[0], LEADER, 1)]
    scheduler_input = make_input(staff_list, required=0, role_reqs=role_reqs)
    errors = check_roles(scheduler_input, [work(2, DATES[0])], role_names={LEADER: "リーダー"})
    assert codes(errors) == ["HC06"]
    assert "リーダー" in errors[0].message
    assert errors[0].role_id == LEADER


def test_hc06_uses_fallback_role_label_without_role_names():
    staff_list = [staff(1, role_id=LEADER)]
    role_reqs = [RoleRequirementInput(DATES[0], LEADER, 1)]
    scheduler_input = make_input(staff_list, required=0, role_reqs=role_reqs)
    errors = check_roles(scheduler_input, [])
    assert codes(errors) == ["HC06"]
    assert f"ロール{LEADER}" in errors[0].message


def test_hc06_satisfied_when_role_requirement_met():
    staff_list = [staff(1, role_id=LEADER)]
    role_reqs = [RoleRequirementInput(DATES[0], LEADER, 1)]
    scheduler_input = make_input(staff_list, required=0, role_reqs=role_reqs)
    errors = check_roles(scheduler_input, [work(1, DATES[0])])
    assert errors == []


def test_missing_role_requirement_row_means_required_zero():
    staff_list = [staff(1, role_id=LEADER)]
    scheduler_input = make_input(staff_list, required=0, role_reqs=())
    errors = check_roles(scheduler_input, [])
    assert errors == []


def test_missing_daily_requirement_skips_role_check():
    staff_list = [staff(1, role_id=LEADER)]
    role_reqs = [RoleRequirementInput(DATES[0], LEADER, 1)]
    scheduler_input = make_input(
        staff_list, required=0, role_reqs=role_reqs, missing_dates=(DATES[0],)
    )
    errors = check_roles(scheduler_input, [])
    assert errors == []


# ---------------------------------------------------------------------------
# HC07 / HC08 hours
# ---------------------------------------------------------------------------


def test_hc07_violated_above_max_minutes():
    s1 = staff(1, minutes=480)
    conditions = [cond(1, max_=480)]
    scheduler_input = make_input([s1], required=0, conditions=conditions)
    assignments = [work(1, d) for d in DATES[:2]]  # 960 > 480
    errors = check_hours(scheduler_input, assignments)
    assert codes(errors) == ["HC07"]
    assert errors[0].staff_id == 1


def test_hc07_satisfied_when_exactly_max_minutes():
    s1 = staff(1, minutes=480)
    conditions = [cond(1, max_=480)]
    scheduler_input = make_input([s1], required=0, conditions=conditions)
    assignments = [work(1, DATES[0])]  # exactly 480
    errors = check_hours(scheduler_input, assignments)
    assert errors == []


def test_hc08_violated_below_min_minutes():
    s1 = staff(1, minutes=480)
    conditions = [cond(1, min_=960)]
    scheduler_input = make_input([s1], required=0, conditions=conditions)
    assignments = [work(1, DATES[0])]  # 480 < 960
    errors = check_hours(scheduler_input, assignments)
    assert codes(errors) == ["HC08"]


def test_hc08_satisfied_when_exactly_min_minutes():
    s1 = staff(1, minutes=480)
    conditions = [cond(1, min_=480)]
    scheduler_input = make_input([s1], required=0, conditions=conditions)
    assignments = [work(1, DATES[0])]  # exactly 480
    errors = check_hours(scheduler_input, assignments)
    assert errors == []


def test_missing_monthly_condition_skips_hours_check():
    s1 = staff(1, minutes=480)
    scheduler_input = make_input([s1], required=0, conditions=[])
    assignments = [work(1, d) for d in DATES]  # would blow any reasonable max
    errors = check_hours(scheduler_input, assignments)
    assert errors == []


# ---------------------------------------------------------------------------
# HC09 / HC10 consecutive
# ---------------------------------------------------------------------------


def test_hc09_violated_when_in_month_run_exceeds_max():
    s1 = staff(1, max_consec=3)
    scheduler_input = make_input([s1], required=0)
    assignments = [work(1, d) for d in DATES[:4]]  # 4 > 3
    errors = check_consecutive(scheduler_input, assignments)
    assert codes(errors) == ["HC09"]
    assert errors[0].work_date == DATES[3]


def test_hc09_satisfied_when_run_equals_max():
    s1 = staff(1, max_consec=3)
    scheduler_input = make_input([s1], required=0)
    assignments = [work(1, d) for d in DATES[:3]]  # exactly 3
    errors = check_consecutive(scheduler_input, assignments)
    assert errors == []


def test_hc09_reports_once_per_run():
    s1 = staff(1, max_consec=2)
    scheduler_input = make_input([s1], required=0)
    assignments = [work(1, d) for d in DATES[:5]]  # 5 consecutive, limit 2
    errors = check_consecutive(scheduler_input, assignments)
    assert codes(errors) == ["HC09"]


def test_hc10_carryover_m5_c2_three_days_ok():
    s1 = staff(1, max_consec=5)
    conditions = [cond(1, carryover=2)]
    scheduler_input = make_input([s1], required=0, conditions=conditions)
    assignments = [work(1, d) for d in DATES[:3]]  # 2 + 3 = 5, OK
    errors = check_consecutive(scheduler_input, assignments)
    assert errors == []


def test_hc10_carryover_m5_c2_four_days_ng():
    s1 = staff(1, max_consec=5)
    conditions = [cond(1, carryover=2)]
    scheduler_input = make_input([s1], required=0, conditions=conditions)
    assignments = [work(1, d) for d in DATES[:4]]  # 2 + 4 = 6 > 5
    errors = check_consecutive(scheduler_input, assignments)
    assert codes(errors) == ["HC10"]
    assert errors[0].work_date == DATES[3]


def test_hc10_carryover_m5_c5_one_day_ng():
    s1 = staff(1, max_consec=5)
    conditions = [cond(1, carryover=5)]
    scheduler_input = make_input([s1], required=0, conditions=conditions)
    assignments = [work(1, DATES[0])]  # 5 + 1 = 6 > 5
    errors = check_consecutive(scheduler_input, assignments)
    assert codes(errors) == ["HC10"]
    assert errors[0].work_date == DATES[0]


def test_carryover_does_not_apply_when_day_one_is_off():
    s1 = staff(1, max_consec=5)
    conditions = [cond(1, carryover=5)]
    scheduler_input = make_input([s1], required=0, conditions=conditions)
    # day 1 off -> carryover run is broken, no violation should be reported
    errors = check_consecutive(scheduler_input, [work(1, DATES[0], False)])
    assert errors == []


def test_missing_monthly_condition_uses_carryover_zero():
    s1 = staff(1, max_consec=3)
    scheduler_input = make_input([s1], required=0, conditions=[])
    assignments = [work(1, d) for d in DATES[:3]]  # exactly 3, OK with carryover 0
    errors = check_consecutive(scheduler_input, assignments)
    assert errors == []


# ---------------------------------------------------------------------------
# filtering rules: inactive staff, out-of-month dates, missing cell = off
# ---------------------------------------------------------------------------


def test_inactive_staff_assignments_are_ignored():
    staff_list = [staff(1), staff(2, active=False, off_weekdays=(0, 1, 2, 3, 4, 5, 6))]
    scheduler_input = make_input(staff_list, required=0)
    # inactive staff "working" on an unavailable weekday should not raise HC01
    errors = check_weekday(scheduler_input, [work(2, DATES[0])])
    assert errors == []


def test_inactive_staff_not_counted_in_staffing():
    staff_list = [staff(1), staff(2, active=False)]
    scheduler_input = make_input(
        staff_list, required=0, overrides={DATES[0]: (1, None)}
    )
    # only inactive staff "works" -> should still be short of required
    errors = check_staffing(scheduler_input, [work(2, DATES[0])])
    assert codes(errors) == ["HC03"]


def test_out_of_month_assignments_are_ignored():
    s1 = staff(1, off_weekdays=(0, 1, 2, 3, 4, 5, 6))
    scheduler_input = make_input([s1], required=0)
    errors = check_weekday(scheduler_input, [work(1, "2026-03-01")])
    assert errors == []


def test_missing_cell_counts_as_off():
    staff_list = [staff(1), staff(2)]
    scheduler_input = make_input(
        staff_list, required=0, overrides={DATES[0]: (2, None)}
    )
    # no assignments at all -> both count as off -> shortage of 2
    errors = check_staffing(scheduler_input, [])
    assert codes(errors) == ["HC03"]
    assert "2名不足" in errors[0].message


# ---------------------------------------------------------------------------
# summarize_staff_minutes
# ---------------------------------------------------------------------------


def test_summarize_staff_minutes_counts_only_active_working_days():
    staff_list = [
        staff(1, minutes=480),
        staff(2, minutes=360, active=False),
    ]
    scheduler_input = make_input(staff_list, required=0)
    assignments = [
        work(1, DATES[0]),
        work(1, DATES[1]),
        work(2, DATES[0]),  # inactive, should be excluded
    ]
    result = summarize_staff_minutes(scheduler_input, assignments)
    assert result == {1: 960}


def test_summarize_staff_minutes_zero_when_no_assignments():
    staff_list = [staff(1, minutes=480)]
    scheduler_input = make_input(staff_list, required=0)
    result = summarize_staff_minutes(scheduler_input, [])
    assert result == {1: 0}
