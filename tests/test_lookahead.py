"""期間の境目の先読みのテスト.

10月のバックテストで見つかった例: 前の期間（〜10/14）を作るとき、次の期間の 10/15 に
もう1人のリーダーが休むことを知らずに、平日リーダーを連続勤務の上限まで入れて期間を終えた。
そのため 10/15 はリーダーなしになった。先読みでは、期間の直後で勤務表のない日も一緒に考える。
"""

import pytest

from src import repositories as repo
from src import services
from src.constants import ROLE_LEADER
from src.database import get_connection, initialize_database
from src.day_conditions import resolve_period_conditions
from src.models import (
    GenerationDay,
    GenerationRequest,
    GenerationStaff,
    StaffDatePreferenceInput,
    StaffDetail,
    StaffInput,
)
from src.period_utils import period_dates
from src.shift_generation import generate_shift

LEADER = 1
PERIOD = ["2026-10-12", "2026-10-13", "2026-10-14"]          # 月・火・水
LOOKAHEAD = ["2026-10-15", "2026-10-16", "2026-10-17"]       # 木・金・土
MON, TUE, WED = PERIOD
THU, FRI, SAT = LOOKAHEAD


def leader(staff_id, weekdays, *, prefs=(), max_consecutive=None, target=None):
    detail = StaffDetail(
        staff=StaffInput(staff_id, f"{staff_id:04d}", f"L{staff_id}", LEADER, 4, "清掃", True,
                         "09:00", "15:30"),
        weekdays=tuple(weekdays),
    )
    preferences = {p: StaffDatePreferenceInput(staff_id, p, absolute_off=True) for p in prefs}
    conditions = {c.work_date: c for c in resolve_period_conditions(detail, PERIOD + LOOKAHEAD, preferences)}
    return GenerationStaff(
        staff_id=staff_id, employee_code=f"{staff_id:04d}", staff_name=f"L{staff_id}",
        role_id=LEADER, skill_level=4, role_code=ROLE_LEADER, day_conditions=conditions,
        max_consecutive_days=max_consecutive, target_days_per_week=target,
    )


def leader_day(work_date):
    return GenerationDay(work_date=work_date, requirement_is_set=True,
                         required_total_staff=1, role_requirements={LEADER: 1})


@pytest.fixture
def staff():
    # 平日リーダー: 月〜金・連続3日まで・目安を多め（目安のためにできるだけ多く入れたがる）
    weekday_leader = leader(1, [0, 1, 2, 3, 4], max_consecutive=3, target=7)
    # もう1人: 火・水・木・土・日。水・木は必ず休み
    other_leader = leader(2, [1, 2, 3, 5, 6], prefs=(WED, THU))
    return [weekday_leader, other_leader]


def solve(staff, lookahead):
    return generate_shift(GenerationRequest(
        work_dates=PERIOD, staff=staff, days=[leader_day(d) for d in PERIOD],
        lookahead_days=[leader_day(d) for d in LOOKAHEAD] if lookahead else [],
    ))


def worked(result, staff_id):
    return {a.work_date for a in result.working_assignments(staff_id)}


def test_without_lookahead_the_period_ends_with_a_full_run(staff):
    """先読みがないと、平日リーダーを月〜水の3連勤で終え、次の期間の木曜に入れなくなる."""
    result = solve(staff, lookahead=False)
    assert worked(result, 1) == {MON, TUE, WED}


def test_lookahead_keeps_the_next_days_coverable(staff):
    """先読みすると、火曜をもう1人のリーダーに任せ、平日リーダーが木・金にも入れるようにする."""
    result = solve(staff, lookahead=True)
    assert worked(result, 1) == {MON, WED}
    assert worked(result, 2) == {TUE}
    assert result.total_shortage == 0  # 期間内の不足は増やさない


def test_lookahead_days_are_not_in_the_result(staff):
    result = solve(staff, lookahead=True)
    assert result.work_dates == PERIOD
    assert {a.work_date for a in result.assignments} == set(PERIOD)
    assert [d.work_date for d in result.days] == PERIOD


def test_lookahead_never_adds_a_shortage_inside_the_period():
    """期間内の不足を減らすことが先. 先読みのために期間内を不足にはしない."""
    only = leader(1, [0, 1, 2, 3, 4], max_consecutive=3)
    result = generate_shift(GenerationRequest(
        work_dates=PERIOD, staff=[only], days=[leader_day(d) for d in PERIOD],
        lookahead_days=[leader_day(d) for d in LOOKAHEAD],
    ))
    assert worked(result, 1) == {MON, TUE, WED}
    assert result.total_shortage == 0


# ---------------------------------------------------------------------------
# 先読みする日の決め方（DB）
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(tmp_path / "lookahead.sqlite")
    initialize_database(connection)
    yield connection
    connection.close()


def add_staff(conn, code, max_consecutive=None):
    return repo.create_staff(conn, code, f"S{code}", 3, 3, standard_start_time="09:00",
                             standard_end_time="15:30", weekdays=list(range(7)),
                             max_consecutive_days=max_consecutive)


def test_lookahead_covers_the_longest_run_limit(conn):
    add_staff(conn, "1", max_consecutive=3)
    add_staff(conn, "2", max_consecutive=5)
    dates = period_dates("2026-11-02", 14)
    assert services.build_lookahead_dates(conn, dates) == period_dates("2026-11-16", 5)


def test_no_lookahead_without_run_limits(conn):
    add_staff(conn, "1")
    assert services.build_lookahead_dates(conn, period_dates("2026-11-02", 14)) == []


def test_lookahead_stops_at_a_saved_schedule(conn):
    """勤務表のある日に着いたら止める（その先は保存済みの勤務で境界を扱う）."""
    sid = add_staff(conn, "1", max_consecutive=3)
    _save_day(conn, sid)
    assert services.build_lookahead_dates(conn, period_dates("2026-11-02", 14)) == ["2026-11-16", "2026-11-17"]


def _save_day(conn, staff_id):
    """11/18 に勤務表がある状態を作る（保存の経路は問わない）."""
    with conn:
        conn.execute(
            "INSERT INTO schedule_days (work_date, status, updated_at) VALUES ('2026-11-18', 'DRAFT', '2026-11-01T00:00:00')"
        )
        conn.execute(
            "INSERT INTO schedule_assignments (work_date, staff_id, is_working, start_time, end_time, locked, source, updated_at) "
            "VALUES ('2026-11-18', ?, 1, '09:00', '15:30', 0, 'GENERATED', '2026-11-01T00:00:00')",
            (staff_id,),
        )


def test_regeneration_request_uses_defaults_for_unsaved_lookahead_days(conn):
    add_staff(conn, "1", max_consecutive=2)
    dates = period_dates("2026-11-02", 14)   # 11/15(日) まで
    request = services.build_regeneration_request(conn, dates)
    assert [d.work_date for d in request.lookahead_days] == ["2026-11-16", "2026-11-17"]
    monday = request.lookahead_days[0]
    assert monday.requirement_is_set and monday.required_total_staff == 7
    assert sorted(monday.role_requirements.values()) == [1, 1, 5]
    assert monday.max_total_staff is None
    # 先読みの日の勤務条件も渡す（休み希望を見るため）
    assert "2026-11-16" in request.staff[0].day_conditions
