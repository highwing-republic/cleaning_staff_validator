import pytest

from src.models import (
    DailyRequirementInput,
    LockedAssignmentInput,
    MonthlyConditionInput,
    PreferenceInput,
    RoleRequirementInput,
    SchedulerInput,
    StaffInput,
)
from src.month_utils import get_month_dates
from src.precheck import (
    check_fixed_consecutive,
    check_fixed_hours,
    check_input_conflicts,
    run_precheck,
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
):
    overrides = overrides or {}
    reqs = []
    for d in DATES:
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


def codes(result_or_errors):
    errors = getattr(result_or_errors, "errors", result_or_errors)
    return [e.code for e in errors]


def test_valid_input_is_ok():
    result = run_precheck(make_input([staff(1), staff(2)]))
    assert result.is_ok, result.errors


# ---------------------------------------------------------------------------
# T24 missing data
# ---------------------------------------------------------------------------


def test_pc01_no_active_staff():
    result = run_precheck(make_input([staff(1, active=False)]))
    assert "PC01" in codes(result)


def test_pc02_missing_requirement_date():
    inp = make_input([staff(1)])
    inp.daily_requirements.pop(9)  # 2026-02-10
    result = run_precheck(inp)
    assert codes(result) == ["PC02"]
    assert result.errors[0].work_date == "2026-02-10"
    assert "2月10日" in result.errors[0].message


def test_pc03_missing_monthly_condition_for_active_only():
    inp = make_input(
        [staff(1), staff(2), staff(3, active=False)],
        conditions=[cond(1), MonthlyConditionInput(2, "2026-03", 4800)],
    )
    result = run_precheck(inp)
    assert codes(result) == ["PC03"]
    assert result.errors[0].staff_id == 2


def test_pc04_missing_weekday_rows():
    s = StaffInput(2, "S2", CLEANER, 480, 5, weekday_availability={w: True for w in range(6)})
    result = run_precheck(make_input([staff(1), s]))
    assert codes(result) == ["PC04"]
    assert result.errors[0].staff_id == 2


def test_missing_data_short_circuits_later_checks():
    inp = make_input([staff(1)], required=5)  # PC05 になるはずの条件
    inp.daily_requirements.pop(0)
    assert codes(run_precheck(inp)) == ["PC02"]


# ---------------------------------------------------------------------------
# T25 PC05
# ---------------------------------------------------------------------------


def test_pc05_shortage_message():
    inp = make_input([staff(1), staff(2)], overrides={"2026-02-10": (3, None)})
    result = run_precheck(inp)
    assert codes(result) == ["PC05"]
    err = result.errors[0]
    assert err.work_date == "2026-02-10"
    assert err.message == "2月10日\n最低人数3\n勤務可能2\n\n1名不足しています。"


@pytest.mark.parametrize(
    "extra",
    [
        dict(staff_list=[staff(1), staff(2, active=False)]),
        dict(staff_list=[staff(1), staff(2, off_weekdays=(1,))]),  # 2026-02-10 は火曜(1)
        dict(prefs=[PreferenceInput(2, "2026-02-10", "UNAVAILABLE")]),
        dict(locks=[LockedAssignmentInput(2, "2026-02-10", False)]),
    ],
    ids=["inactive", "weekday", "unavailable", "locked_off"],
)
def test_pc05_excludes_unavailable_staff(extra):
    staff_list = extra.pop("staff_list", [staff(1), staff(2)])
    conditions = [cond(s.staff_id) for s in staff_list if s.active]
    inp = make_input(staff_list, conditions=conditions, overrides={"2026-02-10": (2, None)}, **extra)
    result = run_precheck(inp)
    assert codes(result) == ["PC05"]
    assert "勤務可能1" in result.errors[0].message


def test_pc05_prefer_off_and_locked_work_still_available():
    inp = make_input(
        [staff(1), staff(2)],
        required=2,
        prefs=[PreferenceInput(1, "2026-02-10", "PREFER_OFF")],
        locks=[LockedAssignmentInput(2, "2026-02-10", True)],
    )
    assert run_precheck(inp).is_ok


# ---------------------------------------------------------------------------
# T26 PC06
# ---------------------------------------------------------------------------


def test_pc06_role_shortage():
    inp = make_input(
        [staff(1, LEADER, off_weekdays=(1,)), staff(2)],
        role_reqs=[RoleRequirementInput("2026-02-10", LEADER, 1)],
    )
    result = run_precheck(inp, role_names={LEADER: "リーダー"})
    assert codes(result) == ["PC06"]
    err = result.errors[0]
    assert err.role_id == LEADER
    assert err.message == "2月10日\nリーダー 必要1\n勤務可能0\n\n1名不足しています。"


def test_pc06_role_satisfied():
    inp = make_input(
        [staff(1, LEADER), staff(2)],
        role_reqs=[RoleRequirementInput(d, LEADER, 1) for d in DATES],
    )
    assert run_precheck(inp).is_ok


# ---------------------------------------------------------------------------
# T27 PC07-PC17
# ---------------------------------------------------------------------------


def test_pc07_role_sum_exceeds_required():
    inp = make_input(
        [staff(1, LEADER), staff(2, CHECKER)],
        role_reqs=[
            RoleRequirementInput("2026-02-10", LEADER, 1),
            RoleRequirementInput("2026-02-10", CHECKER, 1),
        ],
    )
    assert codes(check_input_conflicts(inp)) == ["PC07"]


def test_pc08_required_exceeds_max():
    inp = make_input([staff(1), staff(2)], overrides={"2026-02-10": (2, 1)})
    assert codes(check_input_conflicts(inp)) == ["PC08"]


def test_pc09_role_sum_exceeds_max():
    inp = make_input(
        [staff(1, LEADER), staff(2, CHECKER)],
        overrides={"2026-02-10": (2, 1)},
        role_reqs=[
            RoleRequirementInput("2026-02-10", LEADER, 1),
            RoleRequirementInput("2026-02-10", CHECKER, 1),
        ],
    )
    assert sorted(codes(check_input_conflicts(inp))) == ["PC08", "PC09"]


def test_max_equal_to_required_is_ok():
    inp = make_input([staff(1), staff(2)], required=1, max_total=1)
    assert codes(check_input_conflicts(inp)) == []


@pytest.mark.parametrize(
    "condition, expected",
    [
        (cond(1, 4800, min_=4801), "PC10"),
        (cond(1, 4800, max_=4799), "PC11"),
        (cond(1, carryover=-1), "PC12"),
        (cond(1, carryover=6), "PC13"),
    ],
)
def test_pc10_to_pc13(condition, expected):
    inp = make_input([staff(1, max_consec=5)], conditions=[condition])
    assert codes(check_input_conflicts(inp)) == [expected]


@pytest.mark.parametrize(
    "condition",
    [cond(1, 4800, min_=4800, max_=4800, carryover=5), cond(1, carryover=0)],
)
def test_condition_boundaries_ok(condition):
    inp = make_input([staff(1, max_consec=5)], conditions=[condition])
    assert codes(check_input_conflicts(inp)) == []


def test_pc14_locked_work_with_unavailable():
    inp = make_input(
        [staff(1), staff(2)],
        prefs=[PreferenceInput(1, "2026-02-10", "UNAVAILABLE")],
        locks=[LockedAssignmentInput(1, "2026-02-10", True)],
    )
    errors = check_input_conflicts(inp)
    assert codes(errors) == ["PC14"]
    assert errors[0].staff_id == 1


def test_pc15_locked_work_on_weekday_off():
    inp = make_input(
        [staff(1, off_weekdays=(1,)), staff(2)],
        locks=[LockedAssignmentInput(1, "2026-02-10", True)],
    )
    assert codes(check_input_conflicts(inp)) == ["PC15"]


def test_pc16_locked_work_on_zero_day():
    inp = make_input(
        [staff(1), staff(2)],
        overrides={"2026-02-10": (0, None)},
        locks=[LockedAssignmentInput(1, "2026-02-10", True)],
    )
    assert codes(check_input_conflicts(inp)) == ["PC16"]


def test_pc17_locked_work_exceeds_max():
    inp = make_input(
        [staff(1), staff(2), staff(3)],
        overrides={"2026-02-10": (1, 2)},
        locks=[LockedAssignmentInput(s, "2026-02-10", True) for s in (1, 2, 3)],
    )
    assert codes(check_input_conflicts(inp)) == ["PC17"]


def test_locked_off_does_not_trigger_lock_conflicts():
    inp = make_input(
        [staff(1, off_weekdays=(1,)), staff(2)],
        overrides={"2026-02-10": (0, None)},
        prefs=[PreferenceInput(1, "2026-02-10", "UNAVAILABLE")],
        locks=[LockedAssignmentInput(1, "2026-02-10", False)],
    )
    assert codes(check_input_conflicts(inp)) == []


# ---------------------------------------------------------------------------
# T28 PC18
# ---------------------------------------------------------------------------


def _locks(staff_id, days):
    return [LockedAssignmentInput(staff_id, DATES[d - 1], True) for d in days]


def test_pc18_fixed_minutes_exceed_max():
    inp = make_input(
        [staff(1, minutes=480)],
        conditions=[cond(1, 960, max_=960)],
        locks=_locks(1, [2, 4, 6]),  # 1440分
    )
    errors = check_fixed_hours(inp)
    assert codes(errors) == ["PC18"]
    assert errors[0].staff_id == 1


def test_pc18_equal_is_ok_and_no_max_is_ok():
    inp = make_input([staff(1)], conditions=[cond(1, 960, max_=960)], locks=_locks(1, [2, 4]))
    assert check_fixed_hours(inp) == []
    inp = make_input([staff(1)], conditions=[cond(1, 960)], locks=_locks(1, range(1, 20)))
    assert check_fixed_hours(inp) == []


# ---------------------------------------------------------------------------
# T29 PC19
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "max_consec, carryover, days, expected",
    [
        (3, 0, [1, 2, 3], []),
        (3, 0, [1, 2, 3, 4], ["PC19"]),
        (3, 0, [5, 6, 7, 8], ["PC19"]),
        (3, 0, [1, 2, 3, 5, 6, 7], []),  # 間に休みあり
        (3, 2, [1], []),  # 前月2 + 1 = 3
        (3, 2, [1, 2], ["PC19"]),  # 前月2 + 2 = 4
        (3, 2, [2, 3, 4], []),  # 1日が休みなので前月連勤は途切れる
        (3, 3, [1], ["PC19"]),  # C == M なら初日出勤不可
        (5, 2, [1, 2, 3], []),  # 計画書T42: M5 C2 3連勤OK
        (5, 2, [1, 2, 3, 4], ["PC19"]),  # 計画書T42: M5 C2 4連勤NG
        (5, 5, [1], ["PC19"]),  # 計画書T42: M5 C5 1日勤務NG
    ],
)
def test_pc19_fixed_consecutive(max_consec, carryover, days, expected):
    inp = make_input(
        [staff(1, max_consec=max_consec)],
        conditions=[cond(1, carryover=carryover)],
        locks=_locks(1, days),
    )
    assert codes(check_fixed_consecutive(inp)) == expected


def test_pc19_reports_once_per_run():
    inp = make_input([staff(1, max_consec=2)], locks=_locks(1, [1, 2, 3, 4, 5, 10, 11, 12]))
    errors = check_fixed_consecutive(inp)
    assert codes(errors) == ["PC19", "PC19"]
    assert "2月1日から" in errors[0].message
    assert "2月10日から" in errors[1].message


def test_inactive_staff_locks_are_ignored():
    inp = make_input(
        [staff(1), staff(2, active=False, max_consec=1)],
        conditions=[cond(1)],
        locks=_locks(2, [1, 2, 3]),
    )
    assert run_precheck(inp).is_ok
