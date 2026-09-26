"""T91〜T93 / §56: service層を通したE2E."""

import time

import pytest

from src import repositories as repo
from src import services
from src.database import get_connection, initialize_database
from src.demo_data import DEMO_STAFF, DEMO_YEAR_MONTH, seed_demo
from src.month_utils import get_month_dates, weekday_index

YM = DEMO_YEAR_MONTH
DATES = get_month_dates(YM)


@pytest.fixture
def conn():
    c = get_connection(":memory:")
    initialize_database(c)
    yield c
    c.close()


@pytest.fixture
def demo(conn):
    return seed_demo(conn, YM)


def _grid(conn):
    return {(a.staff_id, a.work_date): a for a in repo.load_assignments(conn, YM)}


def test_demo_data_shape(conn, demo):
    staff = repo.list_staff(conn)
    assert len(staff) == 15
    assert [s.role_id for s in staff].count(1) == 3
    assert [s.role_id for s in staff].count(2) == 3
    assert [s.role_id for s in staff].count(3) == 9
    assert len(repo.get_daily_requirements(conn, YM)) == 31
    types = {p.preference_type for p in repo.get_preferences(conn, YM)}
    assert types == {"UNAVAILABLE", "PREFER_OFF", "PREFER_WORK"}
    assert services.run_precheck_for_month(conn, YM).is_ok


def test_normal_flow(conn, demo):
    """T91: 条件 → 希望 → 最低人数 → generate → edit → lock → regenerate → confirm → Excel."""
    started = time.monotonic()
    outcome = services.generate_and_save(conn, YM)
    assert time.monotonic() - started < 11  # §56 Solver全体10秒以内（+構築・保存）
    assert outcome.saved
    assert outcome.result.status in ("OPTIMAL", "FEASIBLE")
    assert services.validate_current_schedule(conn, YM) == []
    _assert_section56(conn)

    # edit（未LOCK）+ lock
    grid = _grid(conn)
    edited = next(k for k, a in grid.items() if not a.is_working and k[1] == DATES[20])
    locked = next(k for k, a in grid.items() if a.is_working and k[1] == DATES[21])
    services.apply_manual_edit(conn, *edited, True)
    services.apply_manual_edit(conn, locked[0], locked[1], False, is_locked=True)

    # regenerate
    assert services.generate_and_save(conn, YM).saved
    grid = _grid(conn)
    assert grid[locked].is_working is False and grid[locked].is_locked  # LOCK維持
    assert grid[edited].source == "OPTIMIZED"  # 未LOCK MANUALは上書き
    assert services.validate_current_schedule(conn, YM) == []
    _assert_section56(conn)

    # confirm → 編集不可
    assert services.confirm_month(conn, YM).confirmed
    with pytest.raises(services.ScheduleConfirmedError):
        services.apply_manual_edit(conn, *edited, True)
    with pytest.raises(services.ScheduleConfirmedError):
        services.generate_and_save(conn, YM)

    # Excel
    from src.export_excel import export_schedule_excel

    data = export_schedule_excel(
        services.load_scheduler_input(conn, YM),
        repo.load_assignments(conn, YM),
        services.get_role_names(conn),
        repo.get_schedule_month(conn, YM),
    )
    assert data[:2] == b"PK"  # xlsx(zip)


def test_infeasible_flow_shows_shortage_and_does_not_relax(conn, demo):
    """T92: 人員不足 → 事前チェックで不足人数を表示し、データを作らない."""
    for staff_id in demo[6:]:
        repo.deactivate_staff(conn, staff_id)
    outcome = services.generate_and_save(conn, YM)
    assert not outcome.saved
    assert outcome.result is None
    pc05 = [e for e in outcome.precheck.errors if e.code == "PC05"]
    assert pc05
    assert "名不足しています。" in pc05[0].message
    assert repo.load_assignments(conn, YM) == []
    assert repo.get_schedule_month(conn, YM) is None


def test_unavailable_always_respected(conn, demo):
    """T93: 絶対休み100%反映."""
    services.generate_and_save(conn, YM)
    grid = _grid(conn)
    unavailable = [p for p in repo.get_preferences(conn, YM) if p.preference_type == "UNAVAILABLE"]
    assert len(unavailable) == 15
    assert all(grid[p.staff_id, p.work_date].is_working is False for p in unavailable)


def _assert_section56(conn):
    """§56 の必須条件を保存済みシフトに対して確認する."""
    inp = services.load_scheduler_input(conn, YM)
    grid = {k: a.is_working for k, a in _grid(conn).items()}
    active = [s for s in inp.staff if s.active]
    reqs = {r.work_date: r for r in inp.daily_requirements}
    conds = {c.staff_id: c for c in inp.monthly_conditions}

    for s in active:
        for d in DATES:
            if not s.is_available_on(weekday_index(d)):
                assert not grid[s.staff_id, d]  # 通常勤務不可曜日 → 出勤0
        days = [d for d in DATES if grid[s.staff_id, d]]
        minutes = len(days) * s.daily_work_minutes
        c = conds[s.staff_id]
        if c.max_monthly_minutes is not None:
            assert minutes <= c.max_monthly_minutes
        if c.min_monthly_minutes is not None:
            assert minutes >= c.min_monthly_minutes
        run = c.carryover_consecutive_days
        for d in DATES:
            run = run + 1 if grid[s.staff_id, d] else 0
            assert run <= s.max_consecutive_days

    for d in DATES:
        total = sum(grid[s.staff_id, d] for s in active)
        assert reqs[d].required_total_staff <= total <= reqs[d].max_total_staff
    for rr in inp.role_requirements:
        assert sum(grid[s.staff_id, rr.work_date] for s in active if s.role_id == rr.role_id) >= rr.required_count


def test_demo_staff_definition_matches_plan():
    roles = [row[1] for row in DEMO_STAFF]
    assert (len(roles), roles.count(1), roles.count(2), roles.count(3)) == (15, 3, 3, 9)
