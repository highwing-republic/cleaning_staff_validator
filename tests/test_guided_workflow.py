"""業務で使うかんたん作成フローのDBテスト。"""

import pytest

from src import guided_workflow as workflow
from src import repositories as repo, services
from src.constants import SCHEDULE_DAY_FINALIZED, SCHEDULE_RUN_INITIAL
from src.database import get_connection, initialize_database
from src.models import DailyRequirementInput, RoleRequirementInput
from src.period_utils import period_dates

DATES = period_dates("2026-10-20", 3)
ALL_WEEKDAYS = [0, 1, 2, 3, 4, 5, 6]


@pytest.fixture
def conn():
    connection = get_connection(":memory:")
    initialize_database(connection)
    workflow.initialize(connection)
    yield connection
    connection.close()


def add_staff(conn, code="001", name="担当", *, max_consecutive_days=None):
    return repo.create_staff(
        conn,
        code,
        name,
        3,
        3,
        "清掃",
        standard_start_time="09:00",
        standard_end_time="15:00",
        weekdays=ALL_WEEKDAYS,
        max_consecutive_days=max_consecutive_days,
    )


def save_requirements(conn, dates=DATES, *, required=1, **kwargs):
    requirements = [
        DailyRequirementInput(work_date=date, required_total_staff=required, **kwargs)
        for date in dates
    ]
    errors = services.save_period_requirements(conn, list(dates), requirements, [])
    assert errors == []


def create_saved_schedule(conn, dates=DATES):
    result = services.generate_schedule(conn, list(dates))
    run_id, errors = services.save_generated_schedule(conn, list(dates), result)
    assert errors == []
    assert run_id is not None
    return run_id


def assignments_snapshot(conn):
    return [
        (a.work_date, a.staff_id, a.is_working, a.start_time, a.end_time, a.locked, a.source)
        for a in repo.list_schedule_assignments(conn, "0000-01-01", "9999-12-31")
    ]


def run_count(conn):
    return conn.execute("SELECT COUNT(*) FROM schedule_runs").fetchone()[0]


def test_period_can_resume_after_database_reconnect(tmp_path):
    database_path = tmp_path / "workflow.sqlite"
    first = get_connection(str(database_path))
    initialize_database(first)
    workflow.initialize(first)
    workflow.start_period(first, DATES[0], len(DATES))
    add_staff(first)
    save_requirements(first)
    workflow.confirm_inputs(first, DATES[0], len(DATES))
    first.close()

    reopened = get_connection(str(database_path))
    initialize_database(reopened)
    workflow.initialize(reopened)
    assert workflow.get_progress(reopened, DATES[0], len(DATES)) == 3
    listed = workflow.list_periods(reopened)
    assert any((period["start_date"], period["days"]) == (DATES[0], len(DATES)) for period in listed)
    assert workflow.start_period(reopened, DATES[0], len(DATES)) == DATES
    reopened.close()


def test_confirm_inputs_succeeds_without_preference_rows(conn):
    add_staff(conn)
    save_requirements(conn)
    workflow.start_period(conn, DATES[0], len(DATES))

    workflow.confirm_inputs(conn, DATES[0], len(DATES))

    assert workflow.get_progress(conn, DATES[0], len(DATES)) == 3


def test_simple_staff_counts_preserve_advanced_requirement_fields(conn):
    add_staff(conn)
    dates = DATES[:1]
    save_requirements(conn, dates, required=2, max_total_staff=4, reserved_rooms=27, note="要確認")
    repo.save_role_requirement(conn, RoleRequirementInput(dates[0], 3, 1))
    fingerprint = workflow.review_fingerprint(conn, dates, "requirements")

    workflow.save_staff_counts(conn, dates, {dates[0]: 3}, fingerprint)

    requirement = services.get_period_requirements(conn, dates)[0].requirement
    assert requirement.required_total_staff == 3
    assert requirement.max_total_staff == 4
    assert requirement.reserved_rooms == 27
    assert requirement.note == "要確認"
    assert repo.list_role_requirements(conn, dates[0], dates[-1]) == [RoleRequirementInput(dates[0], 3, 1)]


def test_preview_save_preserves_fixed_assignment_and_finalized_day(conn):
    staff_id = add_staff(conn)
    add_staff(conn, "002", "担当2")
    save_requirements(conn, required=1)
    create_saved_schedule(conn)
    errors = services.save_manual_schedule_changes(conn, staff_id, {DATES[1]: (False, True)})
    assert errors == []
    with conn:
        conn.execute(
            "UPDATE schedule_days SET status=? WHERE work_date=?",
            (SCHEDULE_DAY_FINALIZED, DATES[0]),
        )

    preview = workflow.make_preview(conn, DATES)
    assert preview.result.has_solution
    run_id = workflow.save_preview(conn, preview)

    assert repo.get_schedule_day(conn, DATES[0]).status == SCHEDULE_DAY_FINALIZED
    fixed = repo.get_schedule_assignment(conn, DATES[1], staff_id)
    assert fixed.is_working is False
    assert fixed.locked is True
    assert repo.get_schedule_run(conn, run_id).run_type != SCHEDULE_RUN_INITIAL
    assert all(repo.get_schedule_day(conn, date) is not None for date in DATES)


def test_regeneration_fills_missing_days_in_a_mixed_period(conn):
    add_staff(conn)
    save_requirements(conn)
    create_saved_schedule(conn, DATES[:1])

    preview = workflow.make_preview(conn, DATES)
    workflow.save_preview(conn, preview)

    assert all(repo.get_schedule_day(conn, date) is not None for date in DATES)


def test_preview_is_rejected_after_requirements_change_without_extra_writes(conn):
    add_staff(conn)
    save_requirements(conn)
    preview = workflow.make_preview(conn, DATES)
    save_requirements(conn, required=2)
    before_rows, before_runs = assignments_snapshot(conn), run_count(conn)

    with pytest.raises(workflow.WorkflowError, match="作成後に入力や勤務表が変わりました"):
        workflow.save_preview(conn, preview)

    assert assignments_snapshot(conn) == before_rows
    assert run_count(conn) == before_runs


def test_preview_is_rejected_after_unlocked_assignment_change_without_extra_writes(conn):
    staff_id = add_staff(conn)
    add_staff(conn, "002", "担当2")
    save_requirements(conn)
    create_saved_schedule(conn)
    preview = workflow.make_preview(conn, DATES)
    current = repo.get_schedule_assignment(conn, DATES[0], staff_id)
    errors = services.save_manual_schedule_changes(
        conn, staff_id, {DATES[0]: (not current.is_working, False)}
    )
    assert errors == []
    before_rows, before_runs = assignments_snapshot(conn), run_count(conn)

    with pytest.raises(workflow.WorkflowError, match="作成後に入力や勤務表が変わりました"):
        workflow.save_preview(conn, preview)

    assert assignments_snapshot(conn) == before_rows
    assert run_count(conn) == before_runs


def test_preview_is_rejected_if_a_missing_day_was_created_without_extra_writes(conn):
    add_staff(conn)
    save_requirements(conn)
    preview = workflow.make_preview(conn, DATES)
    create_saved_schedule(conn, DATES[:1])
    before_rows, before_runs = assignments_snapshot(conn), run_count(conn)

    with pytest.raises(workflow.WorkflowError, match="作成後に入力や勤務表が変わりました"):
        workflow.save_preview(conn, preview)

    assert assignments_snapshot(conn) == before_rows
    assert run_count(conn) == before_runs


def test_manual_change_rejects_exceeding_daily_maximum(conn):
    staff_ids = [add_staff(conn, f"00{index}", f"担当{index}") for index in range(1, 4)]
    save_requirements(conn, DATES[:1], required=2, max_total_staff=2)
    create_saved_schedule(conn, DATES[:1])
    unassigned = next(
        staff_id for staff_id in staff_ids
        if not repo.get_schedule_assignment(conn, DATES[0], staff_id).is_working
    )
    fingerprint = workflow.input_fingerprint(conn, DATES[:1])

    with pytest.raises(workflow.WorkflowError, match="最大人数を超えます"):
        workflow.save_manual_change(
            conn, DATES[:1], unassigned, DATES[0], True, False, fingerprint
        )

    assert repo.get_schedule_assignment(conn, DATES[0], unassigned).is_working is False


def test_manual_change_rejects_consecutive_work_across_both_period_edges(conn):
    staff_id = add_staff(conn, max_consecutive_days=2)
    dates = period_dates("2026-10-20", 3)
    wider_dates = period_dates("2026-10-19", 5)
    save_requirements(conn, wider_dates, required=0)
    create_saved_schedule(conn, wider_dates)
    errors = services.save_manual_schedule_changes(
        conn,
        staff_id,
        {dates[0]: (True, True), dates[1]: (False, False), dates[2]: (True, True)},
    )
    assert errors == []
    fingerprint = workflow.input_fingerprint(conn, dates)

    with pytest.raises(workflow.WorkflowError, match="3日連続の勤務になります"):
        workflow.save_manual_change(conn, dates, staff_id, dates[1], True, False, fingerprint)

    assert repo.get_schedule_assignment(conn, dates[1], staff_id).is_working is False


def test_finalize_requires_acknowledging_shortage(conn):
    add_staff(conn)
    save_requirements(conn, required=2)
    workflow.save_preview(conn, workflow.make_preview(conn, DATES))
    fingerprint = workflow.input_fingerprint(conn, DATES)

    with pytest.raises(workflow.WorkflowError, match="不足などの注意事項を確認"):
        workflow.finalize_period(conn, DATES, fingerprint)

    assert all(repo.get_schedule_day(conn, date).status != SCHEDULE_DAY_FINALIZED for date in DATES)
    fingerprint = workflow.input_fingerprint(conn, DATES)
    workflow.finalize_period(conn, DATES, fingerprint, acknowledge_issues=True)
    assert all(repo.get_schedule_day(conn, date).status == SCHEDULE_DAY_FINALIZED for date in DATES)


def test_finalize_rejects_a_period_with_a_missing_day(conn):
    add_staff(conn)
    save_requirements(conn)
    create_saved_schedule(conn, DATES[:1])
    fingerprint = workflow.input_fingerprint(conn, DATES)

    with pytest.raises(workflow.WorkflowError, match="未作成の日または必要人数が未入力"):
        workflow.finalize_period(conn, DATES, fingerprint)

    assert repo.get_schedule_day(conn, DATES[0]).status != SCHEDULE_DAY_FINALIZED


def test_failed_simple_count_save_rolls_back_partial_requirement_updates(conn):
    add_staff(conn)
    save_requirements(conn, DATES[:2], required=1, max_total_staff=4)
    before = [view.requirement for view in services.get_period_requirements(conn, DATES[:2])]
    fingerprint = workflow.review_fingerprint(conn, DATES[:2], "requirements")

    with pytest.raises(workflow.WorkflowError, match="0以上"):
        workflow.save_staff_counts(conn, DATES[:2], {DATES[0]: 2, DATES[1]: -1}, fingerprint)

    after = [view.requirement for view in services.get_period_requirements(conn, DATES[:2])]
    assert after == before
