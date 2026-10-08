"""シフト生成のService層テスト（DBからSolverまで通す統合テスト）."""

import pytest

from src import repositories as repo
from src import services
from src.constants import (
    GENERATION_ISSUE_UNSET_WORK_TIME,
    GENERATION_STATUS_OK,
    GENERATION_STATUS_REQUIREMENT_MISSING,
    GENERATION_STATUS_SHORTAGE,
    SOLVER_STATUS_OPTIMAL,
)
from src.database import get_connection, initialize_database
from src.generation_display import (
    format_assignment_cell,
    format_required,
    format_role_shortages,
    format_shortage_summary,
    format_skill_shortage,
    format_staff_shortage,
    format_status,
)
from src.models import DailyRequirementInput, RoleRequirementInput, StaffDatePreferenceInput
from src.period_utils import period_dates

LEADER, CHECKER, CLEANER = 1, 2, 3
DATES = period_dates("2026-10-20", 7)
EVERY_WEEKDAY = [0, 1, 2, 3, 4, 5, 6]


@pytest.fixture()
def conn():
    c = get_connection(":memory:")
    initialize_database(c)
    yield c
    c.close()


def add_staff(
    conn,
    employee_code,
    staff_name,
    *,
    role_id=CLEANER,
    skill_level=3,
    standard_start_time="09:00",
    standard_end_time="15:30",
    weekdays=None,
    max_consecutive_days=None,
):
    return repo.create_staff(
        conn, employee_code, staff_name, role_id, skill_level, "清掃",
        standard_start_time=standard_start_time,
        standard_end_time=standard_end_time,
        weekdays=EVERY_WEEKDAY if weekdays is None else weekdays,
        max_consecutive_days=max_consecutive_days,
    )


def set_requirements(conn, dates, required_total_staff=2, **kwargs):
    role_requirements = kwargs.pop("role_requirements", {})
    repo.save_period_requirements(
        conn,
        DATES,
        [
            DailyRequirementInput(
                work_date=d, required_total_staff=required_total_staff, **kwargs
            )
            for d in dates
        ],
        [
            RoleRequirementInput(d, role_id, count)
            for d in dates
            for role_id, count in role_requirements.items()
        ],
    )


# ---------------------------------------------------------------------------
# build_generation_request
# ---------------------------------------------------------------------------


def test_request_collects_active_staff_only(conn):
    active = add_staff(conn, "0001", "Aさん")
    inactive = add_staff(conn, "0002", "Bさん")
    repo.deactivate_staff(conn, inactive)

    request = services.build_generation_request(conn, DATES)
    assert [s.staff_id for s in request.staff] == [active]


def test_request_folds_weekday_pattern_into_day_conditions(conn):
    """Solverは曜日を直接見ないため、実効条件へ畳み込んで渡すこと."""
    staff_id = add_staff(conn, "0001", "Aさん", weekdays=[1])   # 火曜のみ
    request = services.build_generation_request(conn, DATES)
    conditions = request.staff[0].day_conditions

    assert len(conditions) == len(DATES)
    assert conditions["2026-10-20"].can_work is True        # 火
    assert conditions["2026-10-21"].can_work is False       # 水
    assert request.staff[0].staff_id == staff_id


def test_request_folds_preferences_into_day_conditions(conn):
    staff_id = add_staff(conn, "0001", "Aさん")
    repo.save_staff_period_preferences(
        conn, staff_id, DATES,
        [
            StaffDatePreferenceInput(staff_id, "2026-10-21", absolute_off=True),
            StaffDatePreferenceInput(staff_id, "2026-10-22", override_end_time="13:00"),
        ],
    )
    conditions = services.build_generation_request(conn, DATES).staff[0].day_conditions
    assert conditions["2026-10-21"].can_work is False
    assert conditions["2026-10-22"].effective_end_time == "13:00"


def test_request_carries_max_consecutive_days(conn):
    add_staff(conn, "0001", "Aさん", max_consecutive_days=4)
    request = services.build_generation_request(conn, DATES)
    assert request.staff[0].max_consecutive_days == 4


def test_request_marks_requirement_missing_days(conn):
    add_staff(conn, "0001", "Aさん")
    set_requirements(conn, DATES[:3], required_total_staff=2)

    request = services.build_generation_request(conn, DATES)
    by_date = {d.work_date: d for d in request.days}
    assert by_date["2026-10-20"].requirement_is_set is True
    assert by_date["2026-10-20"].required_total_staff == 2
    assert by_date["2026-10-25"].requirement_is_set is False
    assert by_date["2026-10-25"].required_total_staff == 0


def test_request_carries_role_and_skill_requirements(conn):
    add_staff(conn, "0001", "Aさん")
    set_requirements(
        conn, DATES[:1], required_total_staff=2,
        required_skill_level=4, required_skill_count=1,
        role_requirements={LEADER: 1},
    )
    day = services.build_generation_request(conn, DATES).days[0]
    assert day.role_requirements == {LEADER: 1}
    assert (day.required_skill_level, day.required_skill_count) == (4, 1)


def test_request_carries_reserved_rooms_without_using_it(conn):
    add_staff(conn, "0001", "Aさん")
    set_requirements(conn, DATES[:1], required_total_staff=1, reserved_rooms=12)
    assert services.build_generation_request(conn, DATES).days[0].reserved_rooms == 12


def test_request_with_empty_period(conn):
    request = services.build_generation_request(conn, [])
    assert request.work_dates == []
    assert request.staff == []
    assert request.days == []


def test_request_accepts_prior_work_history(conn):
    staff_id = add_staff(conn, "0001", "Aさん", max_consecutive_days=2)
    prior = {staff_id: {"2026-10-18", "2026-10-19"}}
    request = services.build_generation_request(conn, DATES, prior_work_history=prior)
    assert request.prior_work_history == prior


# ---------------------------------------------------------------------------
# generate_schedule（DB → Solver → 結果）
# ---------------------------------------------------------------------------


def test_generate_schedule_meets_requirements(conn):
    for code, name in (("0001", "Aさん"), ("0002", "Bさん"), ("0003", "Cさん")):
        add_staff(conn, code, name)
    set_requirements(conn, DATES, required_total_staff=2)

    result = services.generate_schedule(conn, DATES)
    assert result.solver_status == SOLVER_STATUS_OPTIMAL
    assert result.total_shortage == 0
    assert all(d.scheduled_staff_count == 2 for d in result.days)
    assert all(d.status == GENERATION_STATUS_OK for d in result.days)


def test_generate_schedule_does_not_overstaff(conn):
    for index in range(1, 6):
        add_staff(conn, f"000{index}", f"S{index}")
    set_requirements(conn, DATES, required_total_staff=2)

    result = services.generate_schedule(conn, DATES)
    assert all(d.scheduled_staff_count == 2 for d in result.days)
    assert result.total_workdays == 2 * len(DATES)


def test_generate_schedule_reports_staff_shortage(conn):
    add_staff(conn, "0001", "Aさん")
    set_requirements(conn, DATES, required_total_staff=3)

    result = services.generate_schedule(conn, DATES)
    assert result.has_solution
    assert all(d.staff_shortage == 2 for d in result.days)
    assert all(d.status == GENERATION_STATUS_SHORTAGE for d in result.days)


def test_generate_schedule_reports_role_shortage(conn):
    add_staff(conn, "0001", "Aさん", role_id=CLEANER)
    set_requirements(
        conn, DATES, required_total_staff=1, role_requirements={LEADER: 1}
    )
    result = services.generate_schedule(conn, DATES)
    assert all(d.role_shortages == {LEADER: 1} for d in result.days)
    assert all(d.scheduled_staff_count == 1 for d in result.days)


def test_generate_schedule_reports_skill_shortage(conn):
    add_staff(conn, "0001", "Aさん", skill_level=2)
    set_requirements(
        conn, DATES, required_total_staff=1, required_skill_level=4, required_skill_count=1
    )
    result = services.generate_schedule(conn, DATES)
    assert all(d.skill_shortage == 1 for d in result.days)


def test_generate_schedule_respects_absolute_off(conn):
    staff_id = add_staff(conn, "0001", "Aさん")
    add_staff(conn, "0002", "Bさん")
    repo.save_staff_period_preferences(
        conn, staff_id, DATES,
        [StaffDatePreferenceInput(staff_id, "2026-10-21", absolute_off=True, note="通院")],
    )
    set_requirements(conn, DATES, required_total_staff=1)

    result = services.generate_schedule(conn, DATES)
    assert "2026-10-21" not in {a.work_date for a in result.working_assignments(staff_id)}
    assert result.total_shortage == 0


def test_generate_schedule_reflects_early_leave(conn):
    staff_id = add_staff(conn, "0001", "Aさん")
    repo.save_staff_period_preferences(
        conn, staff_id, DATES,
        [StaffDatePreferenceInput(staff_id, "2026-10-22", override_end_time="13:00")],
    )
    set_requirements(conn, DATES, required_total_staff=1)

    result = services.generate_schedule(conn, DATES)
    times = {
        a.work_date: (a.start_time, a.end_time)
        for a in result.working_assignments(staff_id)
    }
    assert times["2026-10-20"] == ("09:00", "15:30")
    assert times["2026-10-22"] == ("09:00", "13:00")


def test_generate_schedule_uses_available_extra(conn):
    staff_id = add_staff(conn, "0001", "Aさん", weekdays=[1])   # 火曜のみ
    repo.save_staff_period_preferences(
        conn, staff_id, DATES,
        [StaffDatePreferenceInput(staff_id, "2026-10-21", available_extra=True)],
    )
    set_requirements(conn, DATES[:2], required_total_staff=1)

    result = services.generate_schedule(conn, DATES)
    worked = {a.work_date for a in result.working_assignments(staff_id)}
    assert worked == {"2026-10-20", "2026-10-21"}
    assert result.total_shortage == 0


def test_generate_schedule_excludes_staff_without_standard_time(conn):
    add_staff(conn, "0001", "Aさん", standard_start_time=None, standard_end_time=None)
    set_requirements(conn, DATES, required_total_staff=1)

    result = services.generate_schedule(conn, DATES)
    assert result.total_workdays == 0
    assert len(result.issues_with_code(GENERATION_ISSUE_UNSET_WORK_TIME)) == len(DATES)
    assert all(d.staff_shortage == 1 for d in result.days)


def test_generate_schedule_marks_requirement_missing_days(conn):
    add_staff(conn, "0001", "Aさん")
    set_requirements(conn, DATES[:2], required_total_staff=1)

    result = services.generate_schedule(conn, DATES)
    by_date = {d.work_date: d for d in result.days}
    assert by_date["2026-10-20"].status == GENERATION_STATUS_OK
    assert by_date["2026-10-25"].status == GENERATION_STATUS_REQUIREMENT_MISSING
    assert by_date["2026-10-25"].scheduled_staff_count == 0


def test_generate_schedule_distinguishes_zero_requirement_from_missing(conn):
    add_staff(conn, "0001", "Aさん")
    set_requirements(conn, DATES[:1], required_total_staff=0)

    result = services.generate_schedule(conn, DATES)
    by_date = {d.work_date: d for d in result.days}
    assert by_date["2026-10-20"].status == GENERATION_STATUS_OK
    assert by_date["2026-10-20"].requirement_is_set is True
    assert by_date["2026-10-20"].required_total_staff == 0
    assert by_date["2026-10-21"].status == GENERATION_STATUS_REQUIREMENT_MISSING


def test_generate_schedule_respects_max_consecutive_days(conn):
    for index in range(1, 4):
        add_staff(conn, f"000{index}", f"S{index}", max_consecutive_days=2)
    set_requirements(conn, DATES, required_total_staff=1)

    result = services.generate_schedule(conn, DATES)
    assert result.total_shortage == 0
    for detail in repo.list_staff_details(conn, include_inactive=False):
        worked = sorted(
            a.work_date for a in result.working_assignments(detail.staff.staff_id)
        )
        assert _max_run(worked) <= 2


def _max_run(dates: list[str]) -> int:
    from datetime import timedelta

    from src.period_utils import parse_date

    longest = current = 0
    previous = None
    for work_date in dates:
        value = parse_date(work_date)
        current = current + 1 if previous and value - previous == timedelta(days=1) else 1
        longest = max(longest, current)
        previous = value
    return longest


def test_generate_schedule_respects_max_total_staff(conn):
    for index in range(1, 6):
        add_staff(conn, f"000{index}", f"S{index}")
    set_requirements(conn, DATES, required_total_staff=4, max_total_staff=2)

    result = services.generate_schedule(conn, DATES)
    assert all(d.scheduled_staff_count == 2 for d in result.days)
    assert all(d.staff_shortage == 2 for d in result.days)


def test_generate_schedule_uses_new_role_from_db(conn):
    """DBへ追加したRoleでも固定コードなしで条件に使えること."""
    with conn:
        cur = conn.execute(
            "INSERT INTO roles (role_code, role_name) VALUES ('INSPECTOR', '点検担当')"
        )
    new_role_id = cur.lastrowid
    staff_id = add_staff(conn, "0001", "点検さん", role_id=new_role_id)
    add_staff(conn, "0002", "Aさん", role_id=CLEANER)
    set_requirements(
        conn, DATES, required_total_staff=1, role_requirements={new_role_id: 1}
    )

    result = services.generate_schedule(conn, DATES)
    assert result.total_shortage == 0
    assert len(result.working_assignments(staff_id)) == len(DATES)


def test_generate_schedule_crosses_month_boundary(conn):
    dates = period_dates("2026-10-28", 10)
    add_staff(conn, "0001", "Aさん")
    repo.save_period_requirements(
        conn,
        dates,
        [DailyRequirementInput(work_date=d, required_total_staff=1) for d in dates],
        [],
    )
    result = services.generate_schedule(conn, dates)
    assert [d.work_date for d in result.days] == dates
    assert result.total_shortage == 0
    assert any(d.work_date.startswith("2026-11") for d in result.days)


def test_generate_schedule_with_no_staff(conn):
    set_requirements(conn, DATES, required_total_staff=2)
    result = services.generate_schedule(conn, DATES)
    assert result.has_solution
    assert all(d.staff_shortage == 2 for d in result.days)


def test_generate_schedule_does_not_persist_results(conn):
    """Phase 8では生成結果をDBへ保存しない."""
    add_staff(conn, "0001", "Aさん")
    set_requirements(conn, DATES, required_total_staff=1)
    services.generate_schedule(conn, DATES)

    tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "schedule_runs" not in tables
    assert "schedule_assignments" not in tables


def test_generate_schedule_does_not_change_input_data(conn):
    staff_id = add_staff(conn, "0001", "Aさん")
    repo.save_staff_period_preferences(
        conn, staff_id, DATES,
        [StaffDatePreferenceInput(staff_id, "2026-10-21", absolute_off=True)],
    )
    set_requirements(conn, DATES, required_total_staff=1)

    before_preferences = repo.list_staff_preferences(conn, staff_id, DATES[0], DATES[-1])
    before_requirements = repo.list_daily_requirements(conn, DATES[0], DATES[-1])
    services.generate_schedule(conn, DATES)

    assert repo.list_staff_preferences(conn, staff_id, DATES[0], DATES[-1]) == before_preferences
    assert repo.list_daily_requirements(conn, DATES[0], DATES[-1]) == before_requirements


# ---------------------------------------------------------------------------
# 表示
# ---------------------------------------------------------------------------


def test_assignment_cell_display():
    assert format_assignment_cell("09:00", "15:30", True) == "09:00-15:30"
    assert format_assignment_cell(None, None, False) == "休"
    assert format_assignment_cell("09:00", None, True) == "休"


def test_daily_display_for_a_shortage_day(conn):
    add_staff(conn, "0001", "Aさん", role_id=CLEANER)
    set_requirements(
        conn, DATES, required_total_staff=3, role_requirements={LEADER: 1}
    )
    result = services.generate_schedule(conn, DATES)
    day = result.days[0]
    role_names = services.get_role_names(conn)

    assert format_status(day) == "不足"
    assert format_required(day) == "3"
    assert format_staff_shortage(day) == "2"
    assert format_role_shortages(day, role_names) == "リーダー 1名"
    assert format_skill_shortage(day) == "-"
    assert format_shortage_summary(day, role_names) == "2名不足、リーダー 1名不足"


def test_daily_display_for_a_requirement_missing_day(conn):
    add_staff(conn, "0001", "Aさん")
    result = services.generate_schedule(conn, DATES)
    day = result.days[0]

    assert format_status(day) == "要件未設定"
    assert format_required(day) == "-"
    assert format_staff_shortage(day) == "-"
    assert format_role_shortages(day, {}) == "-"


def test_daily_display_for_a_satisfied_day(conn):
    add_staff(conn, "0001", "Aさん")
    set_requirements(conn, DATES, required_total_staff=1)
    result = services.generate_schedule(conn, DATES)
    day = result.days[0]

    assert format_status(day) == "充足"
    assert format_staff_shortage(day) == "0"
    assert format_shortage_summary(day, {}) == ""
