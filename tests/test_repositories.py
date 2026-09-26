import sqlite3

import pytest

from src.database import get_connection, initialize_database
from src.models import (
    AssignmentResult,
    DailyRequirementInput,
    MonthlyConditionInput,
    PreferenceInput,
    RoleRequirementInput,
    SchedulerResult,
)
from src import repositories as repo


@pytest.fixture()
def conn():
    c = get_connection(":memory:")
    initialize_database(c)
    yield c
    c.close()


# ---------------------------------------------------------------------------
# T13 staff
# ---------------------------------------------------------------------------


def test_list_roles(conn):
    roles = repo.list_roles(conn)
    assert [r["role_code"] for r in roles] == ["LEADER", "CHECKER", "CLEANER"]


def test_create_staff_creates_seven_weekday_rows(conn):
    staff_id = repo.create_staff(conn, "山田", 1, 480, 5)
    rows = conn.execute(
        "SELECT weekday, is_available FROM staff_weekday_availability "
        "WHERE staff_id = ? ORDER BY weekday",
        (staff_id,),
    ).fetchall()
    assert len(rows) == 7
    assert all(row["is_available"] == 1 for row in rows)


def test_get_staff_and_list_staff(conn):
    staff_id = repo.create_staff(conn, "山田", 1, 480, 5)
    s = repo.get_staff(conn, staff_id)
    assert s.staff_name == "山田"
    assert s.active is True
    assert s.weekday_availability == {w: True for w in range(7)}

    all_staff = repo.list_staff(conn)
    assert len(all_staff) == 1

    assert repo.get_staff(conn, 999) is None


def test_list_staff_include_inactive(conn):
    id1 = repo.create_staff(conn, "A", 1, 480, 5)
    id2 = repo.create_staff(conn, "B", 1, 480, 5)
    repo.deactivate_staff(conn, id2)

    all_staff = repo.list_staff(conn, include_inactive=True)
    assert {s.staff_id for s in all_staff} == {id1, id2}

    active_only = repo.list_staff(conn, include_inactive=False)
    assert {s.staff_id for s in active_only} == {id1}


def test_update_staff(conn):
    staff_id = repo.create_staff(conn, "山田", 1, 480, 5)
    repo.update_staff(
        conn,
        staff_id,
        staff_name="鈴木",
        role_id=2,
        daily_work_minutes=360,
        max_consecutive_days=4,
        skill_level=3,
    )
    s = repo.get_staff(conn, staff_id)
    assert s.staff_name == "鈴木"
    assert s.role_id == 2
    assert s.daily_work_minutes == 360
    assert s.max_consecutive_days == 4


def test_update_staff_not_found(conn):
    with pytest.raises(ValueError):
        repo.update_staff(
            conn,
            999,
            staff_name="x",
            role_id=1,
            daily_work_minutes=480,
            max_consecutive_days=5,
            skill_level=3,
        )


# ---------------------------------------------------------------------------
# SK05 skill_level (TEST01, TEST02, TEST05, TEST06)
# ---------------------------------------------------------------------------


def test_create_staff_default_skill_level_is_3(conn):
    staff_id = repo.create_staff(conn, "山田", 1, 480, 5)
    s = repo.get_staff(conn, staff_id)
    assert s.skill_level == 3


@pytest.mark.parametrize("skill_level", [1, 5])
def test_create_staff_with_skill_level_boundaries(conn, skill_level):
    staff_id = repo.create_staff(conn, "山田", 1, 480, 5, skill_level)
    s = repo.get_staff(conn, staff_id)
    assert s.skill_level == skill_level


def test_update_staff_changes_skill_level(conn):
    staff_id = repo.create_staff(conn, "山田", 1, 480, 5, 2)
    repo.update_staff(
        conn,
        staff_id,
        staff_name="山田",
        role_id=1,
        daily_work_minutes=480,
        max_consecutive_days=5,
        skill_level=5,
    )
    s = repo.get_staff(conn, staff_id)
    assert s.skill_level == 5


def test_list_staff_returns_skill_level(conn):
    id1 = repo.create_staff(conn, "A", 1, 480, 5, 1)
    id2 = repo.create_staff(conn, "B", 1, 480, 5, 4)
    skill_by_id = {s.staff_id: s.skill_level for s in repo.list_staff(conn)}
    assert skill_by_id[id1] == 1
    assert skill_by_id[id2] == 4


def test_deactivate_staff(conn):
    staff_id = repo.create_staff(conn, "山田", 1, 480, 5)
    repo.deactivate_staff(conn, staff_id)
    s = repo.get_staff(conn, staff_id)
    assert s.active is False


def test_deactivate_staff_not_found(conn):
    with pytest.raises(ValueError):
        repo.deactivate_staff(conn, 999)


# ---------------------------------------------------------------------------
# T14 weekday
# ---------------------------------------------------------------------------


def test_get_and_save_weekday_availability(conn):
    staff_id = repo.create_staff(conn, "山田", 1, 480, 5)
    availability = repo.get_weekday_availability(conn, staff_id)
    assert availability == {w: True for w in range(7)}

    new_avail = {w: (w not in (5, 6)) for w in range(7)}
    repo.save_weekday_availability(conn, staff_id, new_avail)
    assert repo.get_weekday_availability(conn, staff_id) == new_avail


def test_save_weekday_availability_missing_keys(conn):
    staff_id = repo.create_staff(conn, "山田", 1, 480, 5)
    with pytest.raises(ValueError):
        repo.save_weekday_availability(conn, staff_id, {0: True, 1: True})


def test_save_weekday_availability_extra_keys(conn):
    staff_id = repo.create_staff(conn, "山田", 1, 480, 5)
    with pytest.raises(ValueError):
        repo.save_weekday_availability(conn, staff_id, {w: True for w in range(8)})


def test_save_weekday_availability_overwrites(conn):
    staff_id = repo.create_staff(conn, "山田", 1, 480, 5)
    repo.save_weekday_availability(conn, staff_id, {w: False for w in range(7)})
    repo.save_weekday_availability(conn, staff_id, {w: True for w in range(7)})
    assert repo.get_weekday_availability(conn, staff_id) == {w: True for w in range(7)}


# ---------------------------------------------------------------------------
# T15 monthly conditions
# ---------------------------------------------------------------------------


def test_save_and_get_monthly_condition(conn):
    staff_id = repo.create_staff(conn, "山田", 1, 480, 5)
    cond = MonthlyConditionInput(staff_id=staff_id, year_month="2026-10", target_monthly_minutes=9600)
    repo.save_monthly_condition(conn, cond)
    got = repo.get_monthly_condition(conn, staff_id, "2026-10")
    assert got.target_monthly_minutes == 9600
    assert got.min_monthly_minutes is None

    assert repo.get_monthly_condition(conn, staff_id, "2026-11") is None


def test_save_monthly_condition_upsert(conn):
    staff_id = repo.create_staff(conn, "山田", 1, 480, 5)
    repo.save_monthly_condition(
        conn, MonthlyConditionInput(staff_id=staff_id, year_month="2026-10", target_monthly_minutes=9600)
    )
    repo.save_monthly_condition(
        conn,
        MonthlyConditionInput(
            staff_id=staff_id,
            year_month="2026-10",
            target_monthly_minutes=8000,
            min_monthly_minutes=1000,
            max_monthly_minutes=12000,
            carryover_consecutive_days=2,
        ),
    )
    got = repo.get_monthly_condition(conn, staff_id, "2026-10")
    assert got.target_monthly_minutes == 8000
    assert got.min_monthly_minutes == 1000
    assert got.carryover_consecutive_days == 2

    conditions = repo.get_monthly_conditions(conn, "2026-10")
    assert len(conditions) == 1


def test_get_monthly_conditions_month_filter(conn):
    staff_id = repo.create_staff(conn, "山田", 1, 480, 5)
    repo.save_monthly_condition(
        conn, MonthlyConditionInput(staff_id=staff_id, year_month="2026-10", target_monthly_minutes=9600)
    )
    repo.save_monthly_condition(
        conn, MonthlyConditionInput(staff_id=staff_id, year_month="2026-11", target_monthly_minutes=9600)
    )
    assert len(repo.get_monthly_conditions(conn, "2026-10")) == 1
    assert len(repo.get_monthly_conditions(conn, "2026-11")) == 1
    assert len(repo.get_monthly_conditions(conn, "2026-12")) == 0


# ---------------------------------------------------------------------------
# T16 preferences
# ---------------------------------------------------------------------------


def test_save_get_delete_preference(conn):
    staff_id = repo.create_staff(conn, "山田", 1, 480, 5)
    pref = PreferenceInput(staff_id, "2026-10-15", "PREFER_OFF")
    repo.save_preference(conn, pref)

    prefs = repo.get_preferences(conn, "2026-10")
    assert len(prefs) == 1
    assert prefs[0].preference_type == "PREFER_OFF"

    repo.delete_preference(conn, staff_id, "2026-10-15")
    assert repo.get_preferences(conn, "2026-10") == []


def test_save_preference_upsert(conn):
    staff_id = repo.create_staff(conn, "山田", 1, 480, 5)
    repo.save_preference(conn, PreferenceInput(staff_id, "2026-10-15", "PREFER_OFF"))
    repo.save_preference(conn, PreferenceInput(staff_id, "2026-10-15", "UNAVAILABLE"))
    prefs = repo.get_preferences(conn, "2026-10")
    assert len(prefs) == 1
    assert prefs[0].preference_type == "UNAVAILABLE"


def test_get_preferences_month_filter_excludes_neighbours(conn):
    staff_id = repo.create_staff(conn, "山田", 1, 480, 5)
    repo.save_preference(conn, PreferenceInput(staff_id, "2026-09-30", "PREFER_OFF"))
    repo.save_preference(conn, PreferenceInput(staff_id, "2026-10-01", "PREFER_OFF"))
    repo.save_preference(conn, PreferenceInput(staff_id, "2026-10-31", "PREFER_OFF"))
    repo.save_preference(conn, PreferenceInput(staff_id, "2026-11-01", "PREFER_OFF"))
    prefs = repo.get_preferences(conn, "2026-10")
    dates = {p.work_date for p in prefs}
    assert dates == {"2026-10-01", "2026-10-31"}


# ---------------------------------------------------------------------------
# T17 requirements
# ---------------------------------------------------------------------------


def test_save_get_daily_requirement(conn):
    req = DailyRequirementInput(work_date="2026-10-01", required_total_staff=5)
    repo.save_daily_requirement(conn, req)
    reqs = repo.get_daily_requirements(conn, "2026-10")
    assert len(reqs) == 1
    assert reqs[0].required_total_staff == 5


def test_save_daily_requirement_upsert(conn):
    repo.save_daily_requirement(conn, DailyRequirementInput(work_date="2026-10-01", required_total_staff=5))
    repo.save_daily_requirement(conn, DailyRequirementInput(work_date="2026-10-01", required_total_staff=8))
    reqs = repo.get_daily_requirements(conn, "2026-10")
    assert len(reqs) == 1
    assert reqs[0].required_total_staff == 8


def test_get_daily_requirements_month_filter(conn):
    repo.save_daily_requirement(conn, DailyRequirementInput(work_date="2026-09-30", required_total_staff=1))
    repo.save_daily_requirement(conn, DailyRequirementInput(work_date="2026-10-01", required_total_staff=2))
    repo.save_daily_requirement(conn, DailyRequirementInput(work_date="2026-11-01", required_total_staff=3))
    reqs = repo.get_daily_requirements(conn, "2026-10")
    assert [r.work_date for r in reqs] == ["2026-10-01"]


def test_save_daily_requirements_bulk_all_or_nothing(conn):
    good = DailyRequirementInput(work_date="2026-10-01", required_total_staff=5)
    # required_total_staff NOT NULL -> passing None violates the constraint
    bad = DailyRequirementInput(work_date="2026-10-02", required_total_staff=None)
    with pytest.raises(sqlite3.IntegrityError):
        repo.save_daily_requirements(conn, [good, bad])
    assert repo.get_daily_requirements(conn, "2026-10") == []


def test_save_get_role_requirement(conn):
    req = RoleRequirementInput(work_date="2026-10-01", role_id=1, required_count=2)
    repo.save_role_requirement(conn, req)
    reqs = repo.get_role_requirements(conn, "2026-10")
    assert len(reqs) == 1
    assert reqs[0].required_count == 2


def test_save_role_requirement_upsert(conn):
    repo.save_role_requirement(conn, RoleRequirementInput(work_date="2026-10-01", role_id=1, required_count=2))
    repo.save_role_requirement(conn, RoleRequirementInput(work_date="2026-10-01", role_id=1, required_count=4))
    reqs = repo.get_role_requirements(conn, "2026-10")
    assert len(reqs) == 1
    assert reqs[0].required_count == 4


def test_save_role_requirements_bulk_all_or_nothing(conn):
    good = RoleRequirementInput(work_date="2026-10-01", role_id=1, required_count=2)
    bad = RoleRequirementInput(work_date="2026-10-02", role_id=999, required_count=1)  # FK violation
    with pytest.raises(sqlite3.IntegrityError):
        repo.save_role_requirements(conn, [good, bad])
    assert repo.get_role_requirements(conn, "2026-10") == []


def test_get_role_requirements_month_filter(conn):
    repo.save_role_requirement(conn, RoleRequirementInput(work_date="2026-09-30", role_id=1, required_count=1))
    repo.save_role_requirement(conn, RoleRequirementInput(work_date="2026-10-15", role_id=1, required_count=1))
    reqs = repo.get_role_requirements(conn, "2026-10")
    assert [r.work_date for r in reqs] == ["2026-10-15"]


# ---------------------------------------------------------------------------
# T18 schedule
# ---------------------------------------------------------------------------


def _feasible_result(assignments):
    return SchedulerResult(
        status="OPTIMAL",
        assignments=assignments,
        completed_stage=5,
        objective_overstaff=0,
        objective_target_deviation=0,
        objective_prefer_off=0,
        objective_prefer_work=0,
        objective_max_deviation=0,
        objective_max_overstaff=0,
    )


def test_save_generated_schedule_stores_v14_objectives(conn):
    staff_id = repo.create_staff(conn, "山田", 1, 480, 5)
    result = SchedulerResult(
        status="OPTIMAL",
        assignments=[AssignmentResult(staff_id, "2026-10-01", True)],
        completed_stage=5,
        objective_overstaff=7,
        objective_target_deviation=3,
        objective_prefer_off=1,
        objective_prefer_work=2,
        objective_max_deviation=2,
        objective_max_overstaff=1,
    )
    repo.save_generated_schedule(conn, "2026-10", result)
    month = repo.get_schedule_month(conn, "2026-10")
    assert (month.objective_overstaff, month.objective_target_deviation) == (7, 3)
    assert (month.objective_max_deviation, month.objective_max_overstaff) == (2, 1)


def test_get_schedule_month_none(conn):
    assert repo.get_schedule_month(conn, "2026-10") is None
    assert repo.is_month_confirmed(conn, "2026-10") is False


def test_save_generated_schedule_and_load(conn):
    staff_id = repo.create_staff(conn, "山田", 1, 480, 5)
    result = _feasible_result([AssignmentResult(staff_id, "2026-10-01", True)])
    repo.save_generated_schedule(conn, "2026-10", result)

    month = repo.get_schedule_month(conn, "2026-10")
    assert month.status == "DRAFT"
    assert month.solver_status == "OPTIMAL"
    assert month.generated_at is not None
    assert month.confirmed_at is None

    assignments = repo.load_assignments(conn, "2026-10")
    assert len(assignments) == 1
    assert assignments[0].is_working is True
    assert assignments[0].source == "OPTIMIZED"
    assert assignments[0].is_locked is False


def test_save_generated_schedule_rejects_infeasible(conn):
    result = SchedulerResult(status="INFEASIBLE")
    with pytest.raises(ValueError):
        repo.save_generated_schedule(conn, "2026-10", result)
    assert repo.get_schedule_month(conn, "2026-10") is None


def test_save_generated_schedule_rejects_unknown_and_keeps_existing_data(conn):
    staff_id = repo.create_staff(conn, "山田", 1, 480, 5)
    repo.save_generated_schedule(
        conn, "2026-10", _feasible_result([AssignmentResult(staff_id, "2026-10-01", True)])
    )
    before = repo.load_assignments(conn, "2026-10")

    unknown_result = SchedulerResult(status="UNKNOWN")
    with pytest.raises(ValueError):
        repo.save_generated_schedule(conn, "2026-10", unknown_result)

    after = repo.load_assignments(conn, "2026-10")
    assert before == after


def test_save_generated_schedule_rejects_out_of_month_date(conn):
    staff_id = repo.create_staff(conn, "山田", 1, 480, 5)
    result = _feasible_result([AssignmentResult(staff_id, "2026-11-01", True)])
    with pytest.raises(ValueError):
        repo.save_generated_schedule(conn, "2026-10", result)


def test_save_generated_schedule_keeps_locked_overwrites_unlocked_manual(conn):
    staff_id = repo.create_staff(conn, "山田", 1, 480, 5)
    # 初回生成
    repo.save_generated_schedule(
        conn,
        "2026-10",
        _feasible_result(
            [
                AssignmentResult(staff_id, "2026-10-01", True),
                AssignmentResult(staff_id, "2026-10-02", False),
            ]
        ),
    )
    # 手動変更 + lock
    repo.update_assignment_manual(conn, staff_id, "2026-10-01", False)
    repo.set_lock(conn, staff_id, "2026-10-01", True)
    # 未lockのMANUAL変更
    repo.update_assignment_manual(conn, staff_id, "2026-10-02", True)

    # 再計算
    repo.save_generated_schedule(
        conn,
        "2026-10",
        _feasible_result(
            [
                AssignmentResult(staff_id, "2026-10-01", True),  # locked -> should be ignored
                AssignmentResult(staff_id, "2026-10-02", False),  # unlocked MANUAL -> overwritten
            ]
        ),
    )

    assignments = {a.work_date: a for a in repo.load_assignments(conn, "2026-10")}
    locked = assignments["2026-10-01"]
    assert locked.is_working is False  # kept from manual+locked
    assert locked.is_locked is True
    assert locked.source == "MANUAL"

    overwritten = assignments["2026-10-02"]
    assert overwritten.is_working is False  # overwritten by OPTIMIZED result
    assert overwritten.is_locked is False
    assert overwritten.source == "OPTIMIZED"


def test_get_locked_assignments(conn):
    staff_id = repo.create_staff(conn, "山田", 1, 480, 5)
    repo.save_generated_schedule(
        conn, "2026-10", _feasible_result([AssignmentResult(staff_id, "2026-10-01", True)])
    )
    repo.set_lock(conn, staff_id, "2026-10-01", True)
    locked = repo.get_locked_assignments(conn, "2026-10")
    assert len(locked) == 1
    assert locked[0].staff_id == staff_id
    assert locked[0].is_working is True


def test_update_assignment_manual_new_row(conn):
    staff_id = repo.create_staff(conn, "山田", 1, 480, 5)
    repo.update_assignment_manual(conn, staff_id, "2026-10-01", True)
    assignments = repo.load_assignments(conn, "2026-10")
    assert len(assignments) == 1
    assert assignments[0].source == "MANUAL"
    assert assignments[0].is_locked is False


def test_set_lock_not_found(conn):
    staff_id = repo.create_staff(conn, "山田", 1, 480, 5)
    with pytest.raises(ValueError):
        repo.set_lock(conn, staff_id, "2026-10-01", True)


def test_confirm_schedule_month(conn):
    staff_id = repo.create_staff(conn, "山田", 1, 480, 5)
    repo.save_generated_schedule(
        conn, "2026-10", _feasible_result([AssignmentResult(staff_id, "2026-10-01", True)])
    )
    repo.confirm_schedule_month(conn, "2026-10")
    month = repo.get_schedule_month(conn, "2026-10")
    assert month.status == "CONFIRMED"
    assert month.confirmed_at is not None
    assert repo.is_month_confirmed(conn, "2026-10") is True


def test_confirm_schedule_month_not_found(conn):
    with pytest.raises(ValueError):
        repo.confirm_schedule_month(conn, "2026-10")


def test_confirm_schedule_month_already_confirmed(conn):
    staff_id = repo.create_staff(conn, "山田", 1, 480, 5)
    repo.save_generated_schedule(
        conn, "2026-10", _feasible_result([AssignmentResult(staff_id, "2026-10-01", True)])
    )
    repo.confirm_schedule_month(conn, "2026-10")
    with pytest.raises(repo.ScheduleConfirmedError):
        repo.confirm_schedule_month(conn, "2026-10")


@pytest.mark.parametrize(
    "op",
    [
        lambda conn, staff_id: repo.save_generated_schedule(
            conn, "2026-10", _feasible_result([AssignmentResult(staff_id, "2026-10-01", True)])
        ),
        lambda conn, staff_id: repo.update_assignment_manual(conn, staff_id, "2026-10-01", False),
        lambda conn, staff_id: repo.set_lock(conn, staff_id, "2026-10-01", False),
    ],
)
def test_writes_rejected_on_confirmed_month_and_data_unchanged(conn, op):
    staff_id = repo.create_staff(conn, "山田", 1, 480, 5)
    repo.save_generated_schedule(
        conn, "2026-10", _feasible_result([AssignmentResult(staff_id, "2026-10-01", True)])
    )
    repo.confirm_schedule_month(conn, "2026-10")
    before = repo.load_assignments(conn, "2026-10")
    before_month = repo.get_schedule_month(conn, "2026-10")

    with pytest.raises(repo.ScheduleConfirmedError):
        op(conn, staff_id)

    after = repo.load_assignments(conn, "2026-10")
    after_month = repo.get_schedule_month(conn, "2026-10")
    assert before == after
    assert before_month == after_month
