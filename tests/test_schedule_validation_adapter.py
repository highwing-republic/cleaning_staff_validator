"""保存済み勤務表（current schedule）の検証テスト（Phase 11）.

指示書のV01〜V12に対応するテストには対応番号をコメントで示す。
検証対象は schedule_assignments で、勤怠CSV（attendance_shifts）ではない。
"""

import pytest

from src import repositories as repo
from src import services
from src.constants import (
    VALIDATION_STATUS_ERROR,
    VALIDATION_STATUS_INFO,
    VALIDATION_STATUS_OK,
    VALIDATION_STATUS_WARNING,
)
from src.database import get_connection, initialize_database
from src.models import DailyRequirementInput, RoleRequirementInput, StaffDatePreferenceInput
from src.period_utils import period_dates
from src.schedule_validation_adapter import (
    SCHEDULE_DRAFT,
    SCHEDULE_INCOMPLETE,
    SCHEDULE_MISSING,
)
from src.staffing_validation import (
    REQUIREMENT_MISSING,
    ROLE_SHORTAGE,
    SKILL_SHORTAGE,
    STAFF_OVER_MAX,
    STAFF_SHORTAGE,
)

LEADER, CHECKER, CLEANER = 1, 2, 3
EVERY_WEEKDAY = [0, 1, 2, 3, 4, 5, 6]

DATES = period_dates("2026-10-20", 3)
DAY = DATES[0]


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
):
    return repo.create_staff(
        conn, employee_code, staff_name, role_id, skill_level, "清掃",
        standard_start_time=standard_start_time,
        standard_end_time=standard_end_time,
        weekdays=EVERY_WEEKDAY,
    )


def add_staff_many(conn, count, *, role_id=CLEANER, skill_level=3):
    return [
        add_staff(conn, f"{i:04d}", f"S{i}", role_id=role_id, skill_level=skill_level)
        for i in range(1, count + 1)
    ]


def set_requirements(conn, dates, required_total_staff=1, role_requirements=(), **kwargs):
    repo.save_period_requirements(
        conn,
        list(dates),
        [
            DailyRequirementInput(
                work_date=d, required_total_staff=required_total_staff, **kwargs
            )
            for d in dates
        ],
        [
            RoleRequirementInput(d, role_id, count)
            for d in dates
            for role_id, count in role_requirements
        ],
    )


def save_schedule(conn, dates):
    result = services.generate_schedule(conn, list(dates))
    run_id, errors = services.save_generated_schedule(conn, list(dates), result)
    assert errors == []
    return run_id


def codes(day):
    return [issue.code for issue in day.issues]


def day_of(conn, dates, work_date=None):
    validation = services.validate_current_schedule(conn, list(dates))
    return validation.day(work_date or dates[0])


def issue_of(day, code):
    matches = [i for i in day.issues if i.code == code]
    assert matches, f"{code} が見つかりません: {codes(day)}"
    return matches[0]


# ---------------------------------------------------------------------------
# 人数（V01・V02・V03・V11・V12）
# ---------------------------------------------------------------------------


def test_v01_required_three_scheduled_three_is_ok(conn):
    """V01: 必要3・勤務3 → OK."""
    add_staff_many(conn, 3)
    set_requirements(conn, DATES, required_total_staff=3)
    save_schedule(conn, DATES)
    for work_date in DATES:
        services.finalize_schedule_day(conn, work_date)

    day = day_of(conn, DATES)
    assert day.scheduled_staff == 3
    assert day.staff_shortage == 0
    assert day.status == VALIDATION_STATUS_OK
    assert codes(day) == []


def test_v02_required_three_scheduled_two_reports_shortage(conn):
    """V02: 必要3・勤務2 → 人数不足1."""
    add_staff_many(conn, 2)
    set_requirements(conn, DATES, required_total_staff=3)
    save_schedule(conn, DATES)

    day = day_of(conn, DATES)
    assert day.scheduled_staff == 2
    assert day.staff_shortage == 1
    assert day.status == VALIDATION_STATUS_ERROR
    issue = issue_of(day, STAFF_SHORTAGE)
    assert (issue.severity, issue.required, issue.actual) == (
        VALIDATION_STATUS_ERROR, 3, 2
    )


def test_v03_over_max_is_reported(conn):
    """V03: max2・勤務3 → 最大人数超過."""
    add_staff_many(conn, 3)
    set_requirements(conn, DATES, required_total_staff=3)
    save_schedule(conn, DATES)
    # 勤務表を作ってから上限を2へ下げる（手修正と同じく後から条件が変わる場合）
    set_requirements(conn, DATES, required_total_staff=3, max_total_staff=2)

    day = day_of(conn, DATES)
    issue = issue_of(day, STAFF_OVER_MAX)
    assert issue.severity == VALIDATION_STATUS_ERROR
    assert day.status == VALIDATION_STATUS_ERROR


def test_v11_zero_required_and_zero_scheduled_is_ok(conn):
    """V11: 必要0・勤務0 → 正常（要件未設定とは区別する）."""
    add_staff_many(conn, 2)
    set_requirements(conn, DATES, required_total_staff=0)
    save_schedule(conn, DATES)

    day = day_of(conn, DATES)
    assert day.requirement_defined is True
    assert day.scheduled_staff == 0
    assert REQUIREMENT_MISSING not in codes(day)
    assert day.has_problem is False


def test_v12_reserved_rooms_does_not_affect_the_judgement(conn):
    """V12: 予約0室・必要3・勤務3 → 正常（予約室数は判定に使わない）."""
    add_staff_many(conn, 3)
    set_requirements(conn, DATES, required_total_staff=3, reserved_rooms=0)
    save_schedule(conn, DATES)

    day = day_of(conn, DATES)
    assert day.reserved_rooms == 0
    assert day.staff_shortage == 0
    assert day.has_problem is False


def test_reserved_rooms_is_not_used_to_recalculate_required_staff(conn):
    """予約が多くても必要人数は入力値のまま（再計算しない）."""
    add_staff_many(conn, 1)
    set_requirements(conn, DATES, required_total_staff=1, reserved_rooms=40)
    save_schedule(conn, DATES)

    day = day_of(conn, DATES)
    assert (day.required_staff, day.reserved_rooms) == (1, 40)
    assert day.has_problem is False


# ---------------------------------------------------------------------------
# Role（V04）・Skill（V05）
# ---------------------------------------------------------------------------


def test_v04_role_shortage_is_reported(conn):
    """V04: Leader1必要・Leader0 → Role不足."""
    add_staff_many(conn, 1, role_id=CLEANER)
    set_requirements(
        conn, DATES, required_total_staff=1, role_requirements=((LEADER, 1),)
    )
    save_schedule(conn, DATES)

    day = day_of(conn, DATES)
    issue = issue_of(day, ROLE_SHORTAGE)
    assert issue.role_id == LEADER
    assert (issue.required, issue.actual) == (1, 0)
    assert day.status == VALIDATION_STATUS_ERROR


def test_role_is_taken_from_the_current_staff_master(conn):
    """Roleはassignmentに持たせていないため、スタッフマスターの現在値を使う."""
    staff_id = add_staff_many(conn, 1, role_id=LEADER)[0]
    set_requirements(
        conn, DATES, required_total_staff=1, role_requirements=((LEADER, 1),)
    )
    save_schedule(conn, DATES)
    assert ROLE_SHORTAGE not in codes(day_of(conn, DATES))

    repo.update_staff(
        conn, staff_id,
        employee_code="0001", staff_name="S1", role_id=CLEANER, skill_level=3,
        department="清掃", standard_start_time="09:00", standard_end_time="15:30",
        weekdays=EVERY_WEEKDAY,
    )
    assert ROLE_SHORTAGE in codes(day_of(conn, DATES))


def test_v05_skill_shortage_is_reported(conn):
    """V05: スキル4以上が2名必要・1名 → Skill不足1."""
    add_staff(conn, "0001", "高スキル", skill_level=4)
    add_staff(conn, "0002", "低スキル", skill_level=2)
    set_requirements(
        conn, DATES, required_total_staff=2,
        required_skill_level=4, required_skill_count=2,
    )
    save_schedule(conn, DATES)

    day = day_of(conn, DATES)
    issue = issue_of(day, SKILL_SHORTAGE)
    assert (issue.required, issue.actual) == (2, 1)
    assert day.status == VALIDATION_STATUS_ERROR


def test_skill_is_taken_from_the_current_staff_master(conn):
    staff_id = add_staff(conn, "0001", "Aさん", skill_level=5)
    set_requirements(
        conn, DATES, required_total_staff=1,
        required_skill_level=5, required_skill_count=1,
    )
    save_schedule(conn, DATES)
    assert SKILL_SHORTAGE not in codes(day_of(conn, DATES))

    repo.update_staff(
        conn, staff_id,
        employee_code="0001", staff_name="Aさん", role_id=CLEANER, skill_level=2,
        department="清掃", standard_start_time="09:00", standard_end_time="15:30",
        weekdays=EVERY_WEEKDAY,
    )
    assert SKILL_SHORTAGE in codes(day_of(conn, DATES))


def test_v10_one_person_cannot_fill_two_roles(conn):
    """V10: Leader1 + Checker1 が必要で勤務予定者1名 → 兼任扱いにしない."""
    add_staff_many(conn, 1, role_id=LEADER)
    set_requirements(
        conn, DATES, required_total_staff=1,
        role_requirements=((LEADER, 1), (CHECKER, 1)),
    )
    save_schedule(conn, DATES)

    day = day_of(conn, DATES)
    shortages = [i for i in day.issues if i.code == ROLE_SHORTAGE]
    assert [i.role_id for i in shortages] == [CHECKER]
    assert day.status == VALIDATION_STATUS_ERROR


def test_role_combination_is_satisfied_by_two_people(conn):
    add_staff(conn, "0001", "リーダー", role_id=LEADER)
    add_staff(conn, "0002", "チェッカー", role_id=CHECKER)
    set_requirements(
        conn, DATES, required_total_staff=2,
        role_requirements=((LEADER, 1), (CHECKER, 1)),
    )
    save_schedule(conn, DATES)

    day = day_of(conn, DATES)
    assert [i.code for i in day.issues if i.code == ROLE_SHORTAGE] == []
    assert day.has_problem is False


# ---------------------------------------------------------------------------
# 要件未設定（V06）・勤務表未作成（V07）
# ---------------------------------------------------------------------------


def test_v06_requirement_missing_is_a_warning(conn):
    """V06: 要件行なし → REQUIREMENT_MISSING（WARNING）."""
    add_staff_many(conn, 1)
    set_requirements(conn, DATES[:1], required_total_staff=1)
    save_schedule(conn, DATES[:1])

    # 勤務表はあるが必要条件を消した状態にする
    repo.save_period_requirements(conn, DATES[:1], [], [])
    day = day_of(conn, DATES[:1])
    issue = issue_of(day, REQUIREMENT_MISSING)
    assert issue.severity == VALIDATION_STATUS_WARNING
    assert day.requirement_defined is False
    assert day.status == VALIDATION_STATUS_WARNING


def test_v07_missing_schedule_is_reported(conn):
    """V07: 勤務表なし → SCHEDULE_MISSING（人数の判定は行わない）."""
    add_staff_many(conn, 1)
    set_requirements(conn, DATES, required_total_staff=3)

    day = day_of(conn, DATES)
    assert codes(day) == [SCHEDULE_MISSING]
    assert day.exists is False
    assert day.staffing is None
    assert day.scheduled_staff == 0
    assert day.staff_shortage == 0
    assert day.status == VALIDATION_STATUS_WARNING


def test_missing_schedule_still_shows_the_requirement(conn):
    """未作成日でも必要人数・予約室数は表示できる（判定はしない）."""
    add_staff_many(conn, 1)
    set_requirements(conn, DATES, required_total_staff=3, reserved_rooms=6)

    day = day_of(conn, DATES)
    assert (day.required_staff, day.reserved_rooms) == (3, 6)
    assert STAFF_SHORTAGE not in codes(day)


def test_missing_and_existing_days_are_both_returned(conn):
    add_staff_many(conn, 1)
    set_requirements(conn, DATES, required_total_staff=1)
    save_schedule(conn, DATES[:2])

    validation = services.validate_current_schedule(conn, DATES)
    assert len(validation.days) == len(DATES)
    assert validation.existing_days == 2
    assert validation.missing_days == 1
    assert validation.day(DATES[2]).exists is False


def test_incomplete_schedule_is_detected(conn):
    """§16: 勤務表はあるが有効スタッフの行が欠けている場合を検出する."""
    add_staff_many(conn, 1)
    set_requirements(conn, DATES[:1], required_total_staff=1)
    save_schedule(conn, DATES[:1])
    late = add_staff(conn, "0009", "後から登録さん")

    day = day_of(conn, DATES[:1])
    issue = issue_of(day, SCHEDULE_INCOMPLETE)
    assert issue.severity == VALIDATION_STATUS_WARNING
    assert day.missing_assignment_count == 1
    assert late


# ---------------------------------------------------------------------------
# DRAFT / FINALIZED（V08・V09）
# ---------------------------------------------------------------------------


def test_v08_draft_day_is_reported_as_info(conn):
    """V08: DRAFT → SCHEDULE_DRAFT（エラーではない）."""
    add_staff_many(conn, 1)
    set_requirements(conn, DATES, required_total_staff=1)
    save_schedule(conn, DATES)

    day = day_of(conn, DATES)
    issue = issue_of(day, SCHEDULE_DRAFT)
    assert issue.severity == VALIDATION_STATUS_INFO
    assert day.is_draft is True
    assert day.status == VALIDATION_STATUS_INFO
    assert day.has_problem is False


def test_v09_finalized_day_has_no_draft_issue(conn):
    """V09: FINALIZED → DRAFT issueなし."""
    add_staff_many(conn, 1)
    set_requirements(conn, DATES, required_total_staff=1)
    save_schedule(conn, DATES)
    services.finalize_schedule_day(conn, DAY)

    day = day_of(conn, DATES)
    assert SCHEDULE_DRAFT not in codes(day)
    assert day.is_finalized is True
    assert day.status == VALIDATION_STATUS_OK


def test_finalized_and_draft_are_judged_the_same_way(conn):
    """確定・下書きで体制の判定は変わらない（§18）."""
    add_staff_many(conn, 2)
    set_requirements(conn, DATES, required_total_staff=3)
    save_schedule(conn, DATES)
    services.finalize_schedule_day(conn, DAY)

    validation = services.validate_current_schedule(conn, DATES)
    finalized, draft = validation.day(DAY), validation.day(DATES[1])
    assert finalized.staff_shortage == draft.staff_shortage == 1
    assert STAFF_SHORTAGE in codes(finalized)
    assert STAFF_SHORTAGE in codes(draft)


def test_unfinalize_switches_the_status_back_to_draft(conn):
    """§65: 確定解除が検証結果の表示へすぐ反映される."""
    add_staff_many(conn, 1)
    set_requirements(conn, DATES, required_total_staff=1)
    save_schedule(conn, DATES)
    services.finalize_schedule_day(conn, DAY)
    assert day_of(conn, DATES).is_finalized is True

    services.unfinalize_schedule_day(conn, DAY)
    day = day_of(conn, DATES)
    assert day.is_draft is True
    assert SCHEDULE_DRAFT in codes(day)


def test_period_counts_days_by_state(conn):
    add_staff_many(conn, 1)
    set_requirements(conn, DATES[:2], required_total_staff=1)
    save_schedule(conn, DATES[:2])
    services.finalize_schedule_day(conn, DAY)

    validation = services.validate_current_schedule(conn, DATES)
    assert (validation.finalized_days, validation.draft_days) == (1, 1)
    assert validation.missing_days == 1
    assert validation.clear_days == 2       # 確定OK + 下書きINFO
    assert validation.problem_days == 1     # 未作成日
    assert validation.has_draft is True


# ---------------------------------------------------------------------------
# 手修正・再生成の反映（§19・§20・§63・§64）
# ---------------------------------------------------------------------------


def test_v19_manual_day_off_creates_a_detected_shortage(conn):
    """§19: 3名勤務から1名を手動で休みにすると不足1として検出される."""
    ids = add_staff_many(conn, 3)
    set_requirements(conn, DATES, required_total_staff=3)
    save_schedule(conn, DATES)
    assert day_of(conn, DATES).staff_shortage == 0

    assert services.save_manual_schedule_changes(conn, ids[0], {DAY: (False, True)}) == []
    day = day_of(conn, DATES)
    assert day.scheduled_staff == 2
    assert day.staff_shortage == 1
    assert STAFF_SHORTAGE in codes(day)


def test_locked_and_manual_cells_are_still_validated(conn):
    """§20: locked・source=MANUAL でも不足判定を免除しない."""
    ids = add_staff_many(conn, 3)
    set_requirements(conn, DATES, required_total_staff=3)
    save_schedule(conn, DATES)
    services.save_manual_schedule_changes(conn, ids[0], {DAY: (False, True)})

    assignment = repo.get_schedule_assignment(conn, DAY, ids[0])
    assert (assignment.locked, assignment.source) == (True, "MANUAL")
    assert STAFF_SHORTAGE in codes(day_of(conn, DATES))


def test_validation_reflects_a_regeneration(conn):
    """§64: 再生成の反映後は最新の勤務表を検証する."""
    add_staff_many(conn, 3)
    set_requirements(conn, DATES, required_total_staff=1)
    save_schedule(conn, DATES)
    set_requirements(conn, DATES, required_total_staff=3)
    assert day_of(conn, DATES).staff_shortage == 2

    preview = services.preview_regenerated_schedule(conn, DATES)
    services.apply_regenerated_schedule(conn, DATES, preview)
    day = day_of(conn, DATES)
    assert day.scheduled_staff == 3
    assert day.staff_shortage == 0


def test_manual_working_day_is_counted(conn):
    """休み→出勤の手修正も配置人数に数える."""
    ids = add_staff_many(conn, 2)
    set_requirements(conn, DATES, required_total_staff=1)
    save_schedule(conn, DATES)
    resting = next(
        sid for sid in ids if not repo.get_schedule_assignment(conn, DAY, sid).is_working
    )

    services.save_manual_schedule_changes(conn, resting, {DAY: (True, True)})
    assert day_of(conn, DATES).scheduled_staff == 2


def test_absolute_off_day_without_staff_is_reported_as_shortage(conn):
    """絶対休みで人が足りない日も、通常どおり不足として出す."""
    staff_id = add_staff_many(conn, 1)[0]
    set_requirements(conn, DATES, required_total_staff=1)
    repo.save_staff_period_preferences(
        conn, staff_id, DATES,
        [StaffDatePreferenceInput(staff_id, DAY, absolute_off=True)],
    )
    save_schedule(conn, DATES)

    day = day_of(conn, DATES)
    assert day.scheduled_staff == 0
    assert STAFF_SHORTAGE in codes(day)


# ---------------------------------------------------------------------------
# 勤怠検証との一致（§61）・attendanceと混ぜない（§2）
# ---------------------------------------------------------------------------


def test_v61_matches_attendance_validation_for_the_same_numbers(conn):
    """§61: 同じ人数・Role・Skill条件なら、勤怠検証と同じ不足判定になる."""
    from src.models import AttendanceShiftInput
    from src.staffing_validation import validate_day

    add_staff(conn, "0001", "リーダー", role_id=LEADER, skill_level=5)
    add_staff(conn, "0002", "清掃", role_id=CLEANER, skill_level=2)
    set_requirements(
        conn, DATES, required_total_staff=3,
        required_skill_level=5, required_skill_count=2,
        role_requirements=((LEADER, 1), (CHECKER, 1)),
    )
    save_schedule(conn, DATES)
    schedule_day = day_of(conn, DATES)

    staff = repo.list_staff(conn, include_inactive=True)
    staff_by_id = {s.staff_id: s for s in staff}
    shifts = [
        AttendanceShiftInput(
            employee_code=s.employee_code,
            work_date=DAY,
            raw_shift="09:00-15:30",
            shift_type="TIME_RANGE",
            available_for_cleaning=True,
            staff_id=s.staff_id,
            department="清掃",
        )
        for s in staff
    ]
    attendance_day = validate_day(
        DAY,
        shifts,
        repo.list_daily_requirements(conn, DAY, DAY)[0],
        {LEADER: 1, CHECKER: 1},
        staff_by_id,
        services.get_role_names(conn),
    )

    def summary(issues):
        return sorted(
            (i.code, i.severity, i.required, i.actual, i.role_id)
            for i in issues
            if i.code not in (SCHEDULE_DRAFT,)
        )

    assert summary(schedule_day.issues) == summary(attendance_day.issues)


def test_validation_does_not_touch_attendance_tables(conn):
    """§2: 勤務表の検証で attendance_shifts へ書き込まない."""
    add_staff_many(conn, 1)
    set_requirements(conn, DATES, required_total_staff=1)
    save_schedule(conn, DATES)
    services.validate_current_schedule(conn, DATES)

    assert conn.execute("SELECT count(*) FROM attendance_shifts").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM attendance_imports").fetchone()[0] == 0


def test_validation_does_not_change_the_schedule(conn):
    add_staff_many(conn, 2)
    set_requirements(conn, DATES, required_total_staff=3)
    save_schedule(conn, DATES)
    before = [
        tuple(r)
        for r in conn.execute(
            "SELECT work_date, staff_id, is_working, locked, source FROM schedule_assignments"
            " ORDER BY 1, 2"
        )
    ]
    services.validate_current_schedule(conn, DATES)
    after = [
        tuple(r)
        for r in conn.execute(
            "SELECT work_date, staff_id, is_working, locked, source FROM schedule_assignments"
            " ORDER BY 1, 2"
        )
    ]
    assert after == before


def test_inactive_staff_in_the_schedule_is_still_counted(conn):
    """§37: 無効になったスタッフでも勤務表に行があれば人数に数える."""
    ids = add_staff_many(conn, 2)
    set_requirements(conn, DATES, required_total_staff=2)
    save_schedule(conn, DATES)
    assert day_of(conn, DATES).scheduled_staff == 2

    repo.deactivate_staff(conn, ids[0])
    day = day_of(conn, DATES)
    assert day.scheduled_staff == 2
    assert STAFF_SHORTAGE not in codes(day)


def test_empty_period_returns_no_days(conn):
    validation = services.validate_current_schedule(conn, [])
    assert validation.days == []
    assert validation.work_dates == []


def test_no_staff_and_no_schedule(conn):
    set_requirements(conn, DATES, required_total_staff=2)
    validation = services.validate_current_schedule(conn, DATES)
    assert [codes(d) for d in validation.days] == [[SCHEDULE_MISSING]] * len(DATES)


# ---------------------------------------------------------------------------
# 性能（§95）
# ---------------------------------------------------------------------------


def test_validation_is_fast_for_forty_staff_over_fourteen_days(conn):
    import time

    dates = period_dates("2026-10-20", 14)
    add_staff_many(conn, 40)
    set_requirements(conn, dates, required_total_staff=12)
    save_schedule(conn, dates)

    started = time.perf_counter()
    validation = services.validate_current_schedule(conn, dates)
    elapsed = time.perf_counter() - started

    assert len(validation.days) == 14
    assert elapsed < 3.0
