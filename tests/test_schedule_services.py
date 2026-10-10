"""勤務表の保存・手修正・固定・再生成・確定のテスト（Phase 10）.

指示書のM01〜M20に対応するテストには対応番号をコメントで示す。
ここで扱うのは計画勤務表で、当日の実績（attendance_shifts）ではない。
"""

import pytest

from src import repositories as repo
from src import services
from src.constants import (
    SCHEDULE_DAY_DRAFT,
    SCHEDULE_DAY_FINALIZED,
    SCHEDULE_RUN_INITIAL,
    SCHEDULE_RUN_REGENERATE,
    SCHEDULE_SOURCE_GENERATED,
    SCHEDULE_SOURCE_MANUAL,
    SOLVER_STATUS_INFEASIBLE,
)
from src.database import get_connection, initialize_database
from src.models import (
    DailyRequirementInput,
    ScheduleGenerationResult,
    StaffDatePreferenceInput,
)
from src.period_utils import period_dates
from src.validation import (
    SCHEDULE_ALREADY_EXISTS,
    SCHEDULE_ASSIGNMENT_NOT_FOUND,
    SCHEDULE_DAY_FINALIZED_READONLY,
    SCHEDULE_DAY_NOT_FOUND,
    SCHEDULE_NOTHING_TO_REGENERATE,
    SCHEDULE_STAFF_NOT_FOUND,
    SCHEDULE_STAFF_UNAVAILABLE,
    SCHEDULE_WORK_TIME_UNRESOLVED,
)

LEADER, CHECKER, CLEANER = 1, 2, 3
EVERY_WEEKDAY = [0, 1, 2, 3, 4, 5, 6]

DATES = period_dates("2026-10-20", 14)        # 10/20〜11/02
LATER_DATES = period_dates("2026-10-28", 14)  # 10/28〜11/10（M01: 期間が重なる）
SHORT = period_dates("2026-10-20", 3)


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
    target_days_per_week=None,
):
    return repo.create_staff(
        conn, employee_code, staff_name, role_id, skill_level, "清掃",
        standard_start_time=standard_start_time,
        standard_end_time=standard_end_time,
        weekdays=EVERY_WEEKDAY if weekdays is None else weekdays,
        max_consecutive_days=max_consecutive_days,
        target_days_per_week=target_days_per_week,
    )


def set_requirements(conn, dates, required_total_staff=2, **kwargs):
    repo.save_period_requirements(
        conn,
        list(dates),
        [
            DailyRequirementInput(
                work_date=d, required_total_staff=required_total_staff, **kwargs
            )
            for d in dates
        ],
        [],
    )


def save_schedule(conn, dates, **kwargs):
    """生成してそのまま下書き保存する（テストの前準備）."""
    result = services.generate_schedule(conn, list(dates), **kwargs)
    run_id, errors = services.save_generated_schedule(conn, list(dates), result)
    assert errors == []
    assert run_id is not None
    return run_id


def assignment(conn, work_date, staff_id):
    return repo.get_schedule_assignment(conn, work_date, staff_id)


def working_dates(conn, staff_id, dates=None):
    period = list(dates or DATES)
    return {
        a.work_date
        for a in repo.list_staff_schedule_assignments(conn, staff_id, period[0], period[-1])
        if a.is_working
    }


def snapshot(conn):
    return [
        (a.work_date, a.staff_id, a.is_working, a.start_time, a.end_time, a.locked, a.source)
        for a in repo.list_schedule_assignments(conn, "0000-01-01", "9999-12-31")
    ]


# ---------------------------------------------------------------------------
# 初回保存（M02・M03・M04）
# ---------------------------------------------------------------------------


def test_m02_initial_save_creates_a_day_and_a_row_per_active_staff(conn):
    """M02: 14日保存 → schedule_days 14行・有効スタッフ×14日のassignment."""
    ids = [add_staff(conn, f"000{i}", f"S{i}") for i in range(1, 4)]
    set_requirements(conn, DATES, required_total_staff=2)
    run_id = save_schedule(conn, DATES)

    assert len(repo.list_schedule_days(conn, DATES[0], DATES[-1])) == len(DATES)
    rows = repo.list_schedule_assignments(conn, DATES[0], DATES[-1])
    assert len(rows) == len(ids) * len(DATES)
    assert {r.staff_id for r in rows} == set(ids)
    assert all(r.source == SCHEDULE_SOURCE_GENERATED for r in rows)
    assert all(r.source_run_id == run_id for r in rows)

    run = repo.get_schedule_run(conn, run_id)
    assert run.run_type == SCHEDULE_RUN_INITIAL
    assert (run.period_start, run.period_end) == (DATES[0], DATES[-1])


def test_m02_saved_days_start_as_draft(conn):
    add_staff(conn, "0001", "Aさん")
    set_requirements(conn, SHORT, required_total_staff=1)
    save_schedule(conn, SHORT)
    assert all(
        d.status == SCHEDULE_DAY_DRAFT
        for d in repo.list_schedule_days(conn, SHORT[0], SHORT[-1])
    )


def test_m03_off_staff_are_saved_as_not_working(conn):
    """M03: 休みのスタッフも is_working=0 の行として残る（休み固定のため）."""
    ids = [add_staff(conn, "0001", "Aさん"), add_staff(conn, "0002", "Bさん")]
    set_requirements(conn, SHORT, required_total_staff=1)
    save_schedule(conn, SHORT)

    for staff_id in ids:
        rows = repo.list_staff_schedule_assignments(conn, staff_id, SHORT[0], SHORT[-1])
        assert len(rows) == len(SHORT)

    off_rows = [
        r
        for staff_id in ids
        for r in repo.list_staff_schedule_assignments(conn, staff_id, SHORT[0], SHORT[-1])
        if not r.is_working
    ]
    # 必要1名・候補2名なので、必ず誰かが休みの行として残る
    assert len(off_rows) == len(SHORT)
    assert all(r.start_time is None and r.end_time is None for r in off_rows)


def test_m04_saved_times_are_a_snapshot(conn):
    """M04: 保存後に通常勤務時間を変えても、保存済みの時刻は変わらない."""
    staff_id = add_staff(conn, "0001", "Aさん")
    set_requirements(conn, SHORT, required_total_staff=1)
    save_schedule(conn, SHORT)
    before = assignment(conn, SHORT[0], staff_id)
    assert (before.start_time, before.end_time) == ("09:00", "15:30")

    repo.update_staff(
        conn, staff_id,
        employee_code="0001", staff_name="Aさん", role_id=CLEANER, skill_level=3,
        department="清掃",
        standard_start_time="08:00", standard_end_time="12:00",
        weekdays=EVERY_WEEKDAY,
    )
    after = assignment(conn, SHORT[0], staff_id)
    assert (after.start_time, after.end_time) == ("09:00", "15:30")


def test_initial_save_rejects_a_plan_without_a_solution(conn):
    """解が無い結果は保存しない（空のrunだけ残さない）."""
    add_staff(conn, "0001", "Aさん")
    set_requirements(conn, SHORT, required_total_staff=1)
    no_solution = ScheduleGenerationResult(
        work_dates=list(SHORT), solver_status=SOLVER_STATUS_INFEASIBLE
    )

    run_id, errors = services.save_generated_schedule(conn, SHORT, no_solution)
    assert run_id is None
    assert [e.code for e in errors] == [SCHEDULE_NOTHING_TO_REGENERATE]
    assert repo.list_schedule_days(conn, SHORT[0], SHORT[-1]) == []
    assert repo.list_schedule_runs(conn) == []


def test_initial_save_does_not_touch_actual_attendance(conn):
    """計画勤務表は実績（attendance_shifts）へ書かない（別概念）."""
    add_staff(conn, "0001", "Aさん")
    set_requirements(conn, SHORT, required_total_staff=1)
    save_schedule(conn, SHORT)
    assert conn.execute("SELECT count(*) FROM attendance_shifts").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# 重複するローリング期間（M01）
# ---------------------------------------------------------------------------


def test_m01_overlapping_period_keeps_one_schedule_per_day(conn):
    """M01: 10/20〜11/02 を保存後に 10/28〜11/10 を見ても、10/28が二重にならない."""
    ids = [add_staff(conn, f"000{i}", f"S{i}") for i in range(1, 4)]
    set_requirements(conn, DATES, required_total_staff=2)
    set_requirements(conn, LATER_DATES, required_total_staff=2)
    save_schedule(conn, DATES)

    overlap = "2026-10-28"
    assert len(repo.list_schedule_days(conn, overlap, overlap)) == 1
    assert len(repo.list_schedule_assignments(conn, overlap, overlap)) == len(ids)

    view = services.get_current_schedule(conn, LATER_DATES)
    assert view.existing_dates == [d for d in LATER_DATES if d <= DATES[-1]]
    assert view.missing_dates == [d for d in LATER_DATES if d > DATES[-1]]
    assert len(view.day(overlap).assignments) == len(ids)


def test_m01_initial_save_refuses_to_overwrite_existing_days(conn):
    """既存勤務表がある日を含む期間は初回保存しない（⑦から再生成する）."""
    add_staff(conn, "0001", "Aさん")
    set_requirements(conn, DATES, required_total_staff=1)
    set_requirements(conn, LATER_DATES, required_total_staff=1)
    save_schedule(conn, DATES)
    before = snapshot(conn)

    result = services.generate_schedule(conn, LATER_DATES)
    run_id, errors = services.save_generated_schedule(conn, LATER_DATES, result)
    assert run_id is None
    assert [e.code for e in errors] == [SCHEDULE_ALREADY_EXISTS]
    assert snapshot(conn) == before


def test_initial_save_works_for_a_period_after_the_existing_one(conn):
    """既存期間の続き（重ならない期間）はそのまま保存できる."""
    add_staff(conn, "0001", "Aさん")
    following = period_dates("2026-11-03", 7)
    set_requirements(conn, DATES, required_total_staff=1)
    set_requirements(conn, following, required_total_staff=1)
    save_schedule(conn, DATES)
    save_schedule(conn, following)
    assert len(repo.list_schedule_days(conn, DATES[0], following[-1])) == len(DATES) + len(
        following
    )


# ---------------------------------------------------------------------------
# 手修正（M05・M06・M07・M20）
# ---------------------------------------------------------------------------


def test_m05_manual_change_from_working_to_off(conn):
    """M05: 出勤→休み → source MANUAL・is_working=0."""
    staff_id = add_staff(conn, "0001", "Aさん")
    set_requirements(conn, SHORT, required_total_staff=1)
    save_schedule(conn, SHORT)
    assert assignment(conn, SHORT[0], staff_id).is_working

    errors = services.save_manual_schedule_changes(conn, staff_id, {SHORT[0]: (False, False)})
    assert errors == []
    row = assignment(conn, SHORT[0], staff_id)
    assert row.is_working is False
    assert row.source == SCHEDULE_SOURCE_MANUAL
    assert (row.start_time, row.end_time) == (None, None)


def test_m06_manual_change_to_working_snapshots_resolved_times(conn):
    """M06: 休み→出勤 → resolve_staff_day_conditionの実効時間を保存する."""
    # どちらも 10:00-14:00 にして、休みになった側を手動で出勤させる
    ids = [
        add_staff(conn, "0001", "Aさん", standard_start_time="10:00",
                  standard_end_time="14:00"),
        add_staff(conn, "0002", "Bさん", standard_start_time="10:00",
                  standard_end_time="14:00"),
    ]
    set_requirements(conn, SHORT, required_total_staff=1)
    save_schedule(conn, SHORT)
    resting, off_dates = next(
        (sid, dates)
        for sid, dates in (
            (sid, [d for d in SHORT if not assignment(conn, d, sid).is_working])
            for sid in ids
        )
        if dates
    )

    target = off_dates[0]
    errors = services.save_manual_schedule_changes(conn, resting, {target: (True, False)})
    assert errors == []
    row = assignment(conn, target, resting)
    assert row.is_working is True
    assert (row.start_time, row.end_time) == ("10:00", "14:00")
    assert row.source == SCHEDULE_SOURCE_MANUAL


def test_m06_manual_working_uses_preference_times(conn):
    """早上がり希望がある日は、その実効時間で保存される."""
    staff_id = add_staff(conn, "0001", "Aさん")
    add_staff(conn, "0002", "Bさん")
    set_requirements(conn, SHORT, required_total_staff=1)
    repo.save_staff_period_preferences(
        conn, staff_id, SHORT,
        [StaffDatePreferenceInput(staff_id, SHORT[1], override_end_time="13:00")],
    )
    save_schedule(conn, SHORT)

    # いったん休みにしてから、希望のある日を手動で出勤へ戻す
    services.save_manual_schedule_changes(conn, staff_id, {SHORT[1]: (False, False)})
    services.save_manual_schedule_changes(conn, staff_id, {SHORT[1]: (True, False)})
    row = assignment(conn, SHORT[1], staff_id)
    assert (row.start_time, row.end_time) == ("09:00", "13:00")


def test_m07_manual_working_on_absolute_off_is_rejected(conn):
    """M07: ABSOLUTE_OFFの日を手動出勤 → 保存拒否."""
    staff_id = add_staff(conn, "0001", "Aさん")
    add_staff(conn, "0002", "Bさん")
    set_requirements(conn, SHORT, required_total_staff=1)
    repo.save_staff_period_preferences(
        conn, staff_id, SHORT,
        [StaffDatePreferenceInput(staff_id, SHORT[0], absolute_off=True)],
    )
    save_schedule(conn, SHORT)
    before = snapshot(conn)

    errors = services.save_manual_schedule_changes(conn, staff_id, {SHORT[0]: (True, True)})
    assert [e.code for e in errors] == [SCHEDULE_STAFF_UNAVAILABLE]
    assert snapshot(conn) == before


def test_manual_working_on_a_non_working_weekday_is_rejected(conn):
    staff_id = add_staff(conn, "0001", "Aさん", weekdays=[1])  # 火曜のみ
    add_staff(conn, "0002", "Bさん")
    set_requirements(conn, SHORT, required_total_staff=1)
    save_schedule(conn, SHORT)

    wednesday = SHORT[1]
    errors = services.save_manual_schedule_changes(conn, staff_id, {wednesday: (True, False)})
    assert [e.code for e in errors] == [SCHEDULE_STAFF_UNAVAILABLE]


def test_manual_working_without_standard_time_is_rejected(conn):
    staff_id = add_staff(conn, "0001", "Aさん", standard_start_time=None,
                         standard_end_time=None)
    add_staff(conn, "0002", "Bさん")
    set_requirements(conn, SHORT, required_total_staff=1)
    save_schedule(conn, SHORT)

    errors = services.save_manual_schedule_changes(conn, staff_id, {SHORT[0]: (True, False)})
    assert [e.code for e in errors] == [SCHEDULE_WORK_TIME_UNRESOLVED]


def test_manual_change_to_off_is_allowed_even_when_it_causes_a_shortage(conn):
    """M20: 必要3名・3名勤務から1名を休みにしても保存できる（自動で補充しない）."""
    ids = [add_staff(conn, f"000{i}", f"S{i}") for i in range(1, 4)]
    set_requirements(conn, SHORT, required_total_staff=3)
    save_schedule(conn, SHORT)
    assert all(assignment(conn, SHORT[0], sid).is_working for sid in ids)

    errors = services.save_manual_schedule_changes(conn, ids[0], {SHORT[0]: (False, True)})
    assert errors == []
    working = [sid for sid in ids if assignment(conn, SHORT[0], sid).is_working]
    assert len(working) == 2


def test_manual_change_needs_an_existing_day(conn):
    staff_id = add_staff(conn, "0001", "Aさん")
    set_requirements(conn, SHORT, required_total_staff=1)
    save_schedule(conn, SHORT)
    errors = services.save_manual_schedule_changes(conn, staff_id, {"2026-12-01": (False, False)})
    assert [e.code for e in errors] == [SCHEDULE_DAY_NOT_FOUND]


def test_manual_change_needs_an_existing_assignment(conn):
    staff_id = add_staff(conn, "0001", "Aさん")
    set_requirements(conn, SHORT, required_total_staff=1)
    save_schedule(conn, SHORT)
    late = add_staff(conn, "0009", "後から登録さん")
    errors = services.save_manual_schedule_changes(conn, late, {SHORT[0]: (False, False)})
    assert [e.code for e in errors] == [SCHEDULE_ASSIGNMENT_NOT_FOUND]


def test_manual_change_needs_an_existing_staff(conn):
    add_staff(conn, "0001", "Aさん")
    set_requirements(conn, SHORT, required_total_staff=1)
    save_schedule(conn, SHORT)
    errors = services.save_manual_schedule_changes(conn, 999, {SHORT[0]: (False, False)})
    assert [e.code for e in errors] == [SCHEDULE_STAFF_NOT_FOUND]


def test_manual_change_with_nothing_to_change(conn):
    staff_id = add_staff(conn, "0001", "Aさん")
    set_requirements(conn, SHORT, required_total_staff=1)
    save_schedule(conn, SHORT)
    assert services.save_manual_schedule_changes(conn, staff_id, {}) == []


def test_m16_manual_save_rolls_back_entirely_on_failure(conn, monkeypatch):
    """M16: 14日分の手修正の途中で失敗 → 1日も保存されない."""
    staff_id = add_staff(conn, "0001", "Aさん")
    set_requirements(conn, DATES, required_total_staff=1)
    save_schedule(conn, DATES)
    before = snapshot(conn)

    original = repo._assignment_params
    calls = {"n": 0}

    def exploding(assignment_record, now):
        calls["n"] += 1
        if calls["n"] > 3:
            raise RuntimeError("書き込み失敗")
        return original(assignment_record, now)

    monkeypatch.setattr(repo, "_assignment_params", exploding)
    changes = {d: (False, True) for d in DATES}
    with pytest.raises(RuntimeError):
        services.save_manual_schedule_changes(conn, staff_id, changes)

    assert snapshot(conn) == before


# ---------------------------------------------------------------------------
# 固定（M08・M09・M10）
# ---------------------------------------------------------------------------


def test_m08_locked_working_survives_regeneration(conn):
    """M08: 出勤を固定 → 再生成後も出勤."""
    staff_id = add_staff(conn, "0001", "Aさん", target_days_per_week=1)
    add_staff(conn, "0002", "Bさん")
    set_requirements(conn, SHORT, required_total_staff=1)
    save_schedule(conn, SHORT)
    services.save_manual_schedule_changes(conn, staff_id, {d: (True, True) for d in SHORT})

    preview = services.preview_regenerated_schedule(conn, SHORT)
    services.apply_regenerated_schedule(conn, SHORT, preview)
    assert working_dates(conn, staff_id, SHORT) == set(SHORT)
    assert all(assignment(conn, d, staff_id).locked for d in SHORT)


def test_m09_locked_off_survives_regeneration(conn):
    """M09: 休みを固定 → 不足が出ても再生成後も休み."""
    staff_id = add_staff(conn, "0001", "Aさん")
    set_requirements(conn, SHORT, required_total_staff=1)
    save_schedule(conn, SHORT)
    services.save_manual_schedule_changes(conn, staff_id, {SHORT[0]: (False, True)})

    preview = services.preview_regenerated_schedule(conn, SHORT)
    services.apply_regenerated_schedule(conn, SHORT, preview)
    row = assignment(conn, SHORT[0], staff_id)
    assert row.is_working is False
    assert row.locked is True


def test_m10_unlocked_assignment_can_change_on_regeneration(conn):
    """M10: 固定していないセルは再生成で変更され得る."""
    staff_id = add_staff(conn, "0001", "Aさん")
    other = add_staff(conn, "0002", "Bさん")
    set_requirements(conn, SHORT, required_total_staff=1)
    save_schedule(conn, SHORT)
    # 固定せずに必要人数を増やすと、再生成で休み→出勤になる
    set_requirements(conn, SHORT, required_total_staff=2)

    preview = services.preview_regenerated_schedule(conn, SHORT)
    assert preview.changes
    services.apply_regenerated_schedule(conn, SHORT, preview)
    assert working_dates(conn, staff_id, SHORT) == set(SHORT)
    assert working_dates(conn, other, SHORT) == set(SHORT)


def test_locked_cell_keeps_its_manual_source_after_regeneration(conn):
    staff_id = add_staff(conn, "0001", "Aさん")
    add_staff(conn, "0002", "Bさん")
    set_requirements(conn, SHORT, required_total_staff=1)
    save_schedule(conn, SHORT)
    services.save_manual_schedule_changes(conn, staff_id, {SHORT[0]: (False, True)})

    preview = services.preview_regenerated_schedule(conn, SHORT)
    services.apply_regenerated_schedule(conn, SHORT, preview)
    assert assignment(conn, SHORT[0], staff_id).source == SCHEDULE_SOURCE_MANUAL


# ---------------------------------------------------------------------------
# fixed_assignments（§116 Service integration）
# ---------------------------------------------------------------------------


def test_regeneration_request_fixes_locked_cells_only(conn):
    staff_id = add_staff(conn, "0001", "Aさん")
    add_staff(conn, "0002", "Bさん")
    set_requirements(conn, SHORT, required_total_staff=1)
    save_schedule(conn, SHORT)
    services.save_manual_schedule_changes(conn, staff_id, {SHORT[0]: (False, True)})

    request = services.build_regeneration_request(conn, SHORT)
    assert request.fixed_assignments == {(staff_id, SHORT[0]): 0}


def test_regeneration_request_fixes_every_cell_of_a_finalized_day(conn):
    ids = [add_staff(conn, f"000{i}", f"S{i}") for i in range(1, 3)]
    set_requirements(conn, SHORT, required_total_staff=1)
    save_schedule(conn, SHORT)
    services.finalize_schedule_day(conn, SHORT[0])

    request = services.build_regeneration_request(conn, SHORT)
    fixed_dates = {date for (_sid, date) in request.fixed_assignments}
    assert fixed_dates == {SHORT[0]}
    assert len(request.fixed_assignments) == len(ids)


def test_m19_locked_working_conflicting_with_absolute_off_is_reported(conn):
    """M19: 出勤固定 + ABSOLUTE_OFF → conflictとして表示し、固定を勝手に解除しない."""
    staff_id = add_staff(conn, "0001", "Aさん")
    add_staff(conn, "0002", "Bさん")
    set_requirements(conn, SHORT, required_total_staff=1)
    save_schedule(conn, SHORT)
    services.save_manual_schedule_changes(conn, staff_id, {SHORT[0]: (True, True)})
    # あとから絶対休みの希望が入った
    repo.save_staff_period_preferences(
        conn, staff_id, SHORT,
        [StaffDatePreferenceInput(staff_id, SHORT[0], absolute_off=True)],
    )

    preview = services.preview_regenerated_schedule(conn, SHORT)
    assert [c.work_date for c in preview.conflicts] == [SHORT[0]]
    assert preview.conflicts[0].staff_id == staff_id
    assert not preview.can_apply
    assert assignment(conn, SHORT[0], staff_id).locked is True
    assert assignment(conn, SHORT[0], staff_id).is_working is True


# ---------------------------------------------------------------------------
# 再生成 preview / 反映（M13・M14・M15）
# ---------------------------------------------------------------------------


def test_m13_preview_does_not_change_the_database(conn):
    """M13: preview → DB変更なし."""
    add_staff(conn, "0001", "Aさん")
    add_staff(conn, "0002", "Bさん")
    set_requirements(conn, SHORT, required_total_staff=1)
    save_schedule(conn, SHORT)
    set_requirements(conn, SHORT, required_total_staff=2)

    before = snapshot(conn)
    runs_before = len(repo.list_schedule_runs(conn))
    preview = services.preview_regenerated_schedule(conn, SHORT)

    assert preview.result.has_solution
    assert preview.changes
    assert snapshot(conn) == before
    assert len(repo.list_schedule_runs(conn)) == runs_before


def test_m14_apply_updates_the_database_and_adds_one_run(conn):
    """M14: 反映 → DB更新・schedule_runs 1件追加."""
    add_staff(conn, "0001", "Aさん")
    add_staff(conn, "0002", "Bさん")
    set_requirements(conn, SHORT, required_total_staff=1)
    save_schedule(conn, SHORT)
    set_requirements(conn, SHORT, required_total_staff=2)

    runs_before = len(repo.list_schedule_runs(conn))
    preview = services.preview_regenerated_schedule(conn, SHORT)
    run_id, errors = services.apply_regenerated_schedule(conn, SHORT, preview)

    assert errors == []
    assert len(repo.list_schedule_runs(conn)) == runs_before + 1
    assert repo.get_schedule_run(conn, run_id).run_type == SCHEDULE_RUN_REGENERATE
    for day in repo.list_schedule_days(conn, SHORT[0], SHORT[-1]):
        assert day.latest_run_id == run_id


def test_preview_without_a_saved_schedule_reports_it(conn):
    add_staff(conn, "0001", "Aさん")
    set_requirements(conn, SHORT, required_total_staff=1)
    preview = services.preview_regenerated_schedule(conn, SHORT)
    assert [e.code for e in preview.errors] == [SCHEDULE_DAY_NOT_FOUND]
    assert not preview.can_apply


def test_preview_of_a_fully_finalized_period_reports_it(conn):
    add_staff(conn, "0001", "Aさん")
    set_requirements(conn, SHORT, required_total_staff=1)
    save_schedule(conn, SHORT)
    for d in SHORT:
        services.finalize_schedule_day(conn, d)

    preview = services.preview_regenerated_schedule(conn, SHORT)
    assert [e.code for e in preview.errors] == [SCHEDULE_NOTHING_TO_REGENERATE]
    assert preview.skipped_finalized_dates == list(SHORT)
    assert not preview.can_apply


def test_apply_without_a_usable_preview_changes_nothing(conn):
    add_staff(conn, "0001", "Aさん")
    set_requirements(conn, SHORT, required_total_staff=1)
    save_schedule(conn, SHORT)
    before = snapshot(conn)

    empty = services.preview_regenerated_schedule(conn, [])
    run_id, errors = services.apply_regenerated_schedule(conn, SHORT, empty)
    assert run_id is None
    assert errors
    assert snapshot(conn) == before


def test_m15_apply_rolls_back_entirely_on_failure(conn, monkeypatch):
    """M15: 反映の途中で失敗 → assignmentが一部だけ更新された状態にならない."""
    add_staff(conn, "0001", "Aさん")
    add_staff(conn, "0002", "Bさん")
    set_requirements(conn, DATES, required_total_staff=1)
    save_schedule(conn, DATES)
    set_requirements(conn, DATES, required_total_staff=2)

    preview = services.preview_regenerated_schedule(conn, DATES)
    assert preview.can_apply
    before = snapshot(conn)
    runs_before = len(repo.list_schedule_runs(conn))

    original = repo._assignment_params
    calls = {"n": 0}

    def exploding(assignment_record, now):
        calls["n"] += 1
        if calls["n"] > 5:
            raise RuntimeError("書き込み失敗")
        return original(assignment_record, now)

    monkeypatch.setattr(repo, "_assignment_params", exploding)
    with pytest.raises(RuntimeError):
        services.apply_regenerated_schedule(conn, DATES, preview)

    assert snapshot(conn) == before
    assert len(repo.list_schedule_runs(conn)) == runs_before


def test_regeneration_keeps_phase9_fairness(conn):
    """§94: 再生成でもPhase 9の公平性（target_days_per_week）を使う."""
    week = period_dates("2026-10-20", 7)
    a = add_staff(conn, "0001", "Aさん", target_days_per_week=6)
    b = add_staff(conn, "0002", "Bさん", target_days_per_week=1)
    set_requirements(conn, week, required_total_staff=1)
    save_schedule(conn, week)

    preview = services.preview_regenerated_schedule(conn, week)
    services.apply_regenerated_schedule(conn, week, preview)
    assert len(working_dates(conn, a, week)) == 6
    assert len(working_dates(conn, b, week)) == 1


# ---------------------------------------------------------------------------
# 確定 / 確定解除（M11・M12）
# ---------------------------------------------------------------------------


def test_m11_finalized_day_is_untouched_by_regeneration(conn):
    """M11: FINALIZED日は再生成しても全スタッフ同一."""
    ids = [add_staff(conn, f"000{i}", f"S{i}") for i in range(1, 3)]
    set_requirements(conn, SHORT, required_total_staff=1)
    save_schedule(conn, SHORT)
    services.finalize_schedule_day(conn, SHORT[0])
    frozen = {sid: assignment(conn, SHORT[0], sid).is_working for sid in ids}

    set_requirements(conn, SHORT, required_total_staff=2)
    preview = services.preview_regenerated_schedule(conn, SHORT)
    assert all(c.work_date != SHORT[0] for c in preview.changes)
    services.apply_regenerated_schedule(conn, SHORT, preview)

    assert {sid: assignment(conn, SHORT[0], sid).is_working for sid in ids} == frozen
    assert repo.get_schedule_day(conn, SHORT[0]).status == SCHEDULE_DAY_FINALIZED


def test_finalized_day_rejects_manual_changes(conn):
    staff_id = add_staff(conn, "0001", "Aさん")
    set_requirements(conn, SHORT, required_total_staff=1)
    save_schedule(conn, SHORT)
    services.finalize_schedule_day(conn, SHORT[0])

    errors = services.save_manual_schedule_changes(conn, staff_id, {SHORT[0]: (False, False)})
    assert [e.code for e in errors] == [SCHEDULE_DAY_FINALIZED_READONLY]
    assert assignment(conn, SHORT[0], staff_id).is_working is True


def test_m12_unfinalize_makes_the_day_editable_again(conn):
    """M12: FINALIZED → DRAFT で再び手修正・再生成の対象になる."""
    staff_id = add_staff(conn, "0001", "Aさん")
    set_requirements(conn, SHORT, required_total_staff=1)
    save_schedule(conn, SHORT)
    services.finalize_schedule_day(conn, SHORT[0])
    services.unfinalize_schedule_day(conn, SHORT[0])

    day = repo.get_schedule_day(conn, SHORT[0])
    assert day.status == SCHEDULE_DAY_DRAFT
    assert day.finalized_at is None
    assert services.save_manual_schedule_changes(
        conn, staff_id, {SHORT[0]: (False, False)}
    ) == []
    assert services.get_current_schedule(conn, SHORT).draft_dates == list(SHORT)


def test_finalizing_an_unsaved_day_reports_it(conn):
    assert [e.code for e in services.finalize_schedule_day(conn, SHORT[0])] == [
        SCHEDULE_DAY_NOT_FOUND
    ]
    assert [e.code for e in services.unfinalize_schedule_day(conn, SHORT[0])] == [
        SCHEDULE_DAY_NOT_FOUND
    ]


def test_finalizing_does_not_change_assignments(conn):
    add_staff(conn, "0001", "Aさん")
    set_requirements(conn, SHORT, required_total_staff=1)
    save_schedule(conn, SHORT)
    before = snapshot(conn)
    services.finalize_schedule_day(conn, SHORT[0])
    assert snapshot(conn) == before


# ---------------------------------------------------------------------------
# prior_work_history（M17・M18）
# ---------------------------------------------------------------------------


def test_m17_finalized_prior_days_limit_consecutive_work(conn):
    """M17: 期間前2連勤がFINALIZED・max_consecutive=2 → 期間初日は勤務不可."""
    prior = period_dates("2026-10-18", 2)          # 10/18・10/19
    target = period_dates("2026-10-20", 3)
    staff_id = add_staff(conn, "0001", "Aさん", max_consecutive_days=2)
    set_requirements(conn, prior, required_total_staff=1)
    set_requirements(conn, target, required_total_staff=1)
    save_schedule(conn, prior)
    for d in prior:
        services.finalize_schedule_day(conn, d)
    assert working_dates(conn, staff_id, prior) == set(prior)

    history = services.build_prior_work_history(conn, target)
    assert history == {staff_id: set(prior)}

    result = services.generate_schedule(
        conn, target, prior_work_history=history
    )
    worked = {a.work_date for a in result.working_assignments(staff_id)}
    assert target[0] not in worked


def test_m18_draft_prior_days_are_used_as_history(conn):
    """M18: 期間前の2日がDRAFTでも prior_work_history に使う."""
    prior = period_dates("2026-10-18", 2)
    target = period_dates("2026-10-20", 3)
    staff_id = add_staff(conn, "0001", "Aさん", max_consecutive_days=2)
    set_requirements(conn, prior, required_total_staff=1)
    set_requirements(conn, target, required_total_staff=1)
    save_schedule(conn, prior)
    assert working_dates(conn, staff_id, prior) == set(prior)

    assert services.build_prior_work_history(conn, target) == {staff_id: set(prior)}


@pytest.mark.parametrize("finalize_prior", [True, False], ids=["finalized", "draft"])
def test_generate_schedule_automatically_uses_prior_history(conn, finalize_prior):
    prior = period_dates("2026-10-18", 2)
    target = period_dates("2026-10-20", 3)
    staff_id = add_staff(conn, "0001", "Aさん", max_consecutive_days=2)
    set_requirements(conn, prior, required_total_staff=1)
    set_requirements(conn, target, required_total_staff=1)
    save_schedule(conn, prior)
    if finalize_prior:
        for work_date in prior:
            services.finalize_schedule_day(conn, work_date)

    result = services.generate_schedule(conn, target)

    assert target[0] not in {
        assignment.work_date for assignment in result.working_assignments(staff_id)
    }


def test_prior_history_is_used_by_the_regeneration_request(conn):
    prior = period_dates("2026-10-18", 2)
    target = period_dates("2026-10-20", 3)
    staff_id = add_staff(conn, "0001", "Aさん", max_consecutive_days=2)
    set_requirements(conn, prior, required_total_staff=1)
    set_requirements(conn, target, required_total_staff=1)
    save_schedule(conn, prior)
    for d in prior:
        services.finalize_schedule_day(conn, d)
    save_schedule(conn, target)

    request = services.build_regeneration_request(conn, target)
    assert request.prior_work_history == {staff_id: set(prior)}


def test_following_history_is_used_by_the_regeneration_request(conn):
    target = period_dates("2026-10-20", 1)
    following = period_dates("2026-10-21", 2)
    staff_id = add_staff(conn, "0001", "Aさん", max_consecutive_days=2)
    set_requirements(conn, target, required_total_staff=1)
    set_requirements(conn, following, required_total_staff=1)
    save_schedule(conn, following)
    save_schedule(conn, target)

    request = services.build_regeneration_request(conn, target)

    assert request.following_work_history == {staff_id: set(following)}


def test_prior_history_with_an_empty_period(conn):
    assert services.build_prior_work_history(conn, []) == {}


# ---------------------------------------------------------------------------
# 現在の勤務表ビュー
# ---------------------------------------------------------------------------


def test_current_schedule_marks_missing_days(conn):
    add_staff(conn, "0001", "Aさん")
    set_requirements(conn, SHORT, required_total_staff=1)
    save_schedule(conn, SHORT)

    longer = period_dates("2026-10-20", 5)
    view = services.get_current_schedule(conn, longer)
    assert view.existing_dates == list(SHORT)
    assert view.missing_dates == longer[3:]
    assert view.day(longer[4]).exists is False
    assert view.day(longer[4]).is_editable is False


def test_current_schedule_counts_working_and_locked(conn):
    ids = [add_staff(conn, f"000{i}", f"S{i}") for i in range(1, 4)]
    set_requirements(conn, SHORT, required_total_staff=2)
    save_schedule(conn, SHORT)
    services.save_manual_schedule_changes(conn, ids[0], {SHORT[0]: (True, True)})

    day = services.get_current_schedule(conn, SHORT).day(SHORT[0])
    assert day.working_count == sum(
        1 for sid in ids if assignment(conn, SHORT[0], sid).is_working
    )
    assert day.locked_count == 1


def test_current_schedule_with_an_empty_period(conn):
    view = services.get_current_schedule(conn, [])
    assert view.existing_dates == []
    assert view.missing_dates == []
    assert view.days == []


# ---------------------------------------------------------------------------
# 性能（§118）
# ---------------------------------------------------------------------------


def test_regeneration_preview_is_fast_enough_for_real_data(conn):
    """保存済み 40名 × 14日でも preview が実用的な時間で返ること."""
    staff_ids = [
        add_staff(conn, f"{i:04d}", f"S{i}", target_days_per_week=5,
                  max_consecutive_days=5)
        for i in range(1, 41)
    ]
    set_requirements(conn, DATES, required_total_staff=12)
    save_schedule(conn, DATES)

    # 一部を固定・一部を確定して、実運用に近い状態にする
    services.save_manual_schedule_changes(
        conn, staff_ids[0], {d: (False, True) for d in DATES[:3]}
    )
    services.finalize_schedule_day(conn, DATES[0])

    preview = services.preview_regenerated_schedule(conn, DATES)
    assert preview.result.has_solution
    assert preview.result.solve_seconds < 10
