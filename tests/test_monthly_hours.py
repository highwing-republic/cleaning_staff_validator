"""月ごとの合計勤務時間（スタッフ単位）のテスト."""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src import repositories as repo
from src.database import get_connection, initialize_database
from src.monthly_hours import (
    MonthTotal,
    WorkedShift,
    format_hours,
    format_month_total,
    month_dates,
    monthly_totals,
    months_of,
    totals_by_month,
)
from src.period_utils import period_dates
from src.ui_common import DB_PATH_ENV

APP = str(Path(__file__).resolve().parents[1] / "app.py")


# ---------------------------------------------------------------------------
# 計算・表示
# ---------------------------------------------------------------------------


def test_months_of_a_period_across_months():
    assert months_of(period_dates("2026-10-31", 14)) == ["2026-10", "2026-11"]
    assert months_of(period_dates("2026-11-14", 14)) == ["2026-11"]


def test_month_dates():
    assert len(month_dates("2026-11")) == 30
    assert month_dates("2026-02")[-1] == "2026-02-28"


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [(390, "6.5時間"), (405, "6.75時間"), (600, "10時間"), (0, "0時間"), (6630, "110.5時間")],
)
def test_format_hours(minutes, expected):
    assert format_hours(minutes) == expected


def test_format_month_total():
    assert format_month_total(MonthTotal(17, 6630)) == "110.5時間（17日）"
    assert format_month_total(None) == "0時間"


def test_totals_split_by_month():
    shifts = [
        WorkedShift(1, "2026-10-31", "09:00", "15:30"),
        WorkedShift(1, "2026-11-01", "09:00", "13:00"),
        WorkedShift(1, "2026-11-02", "06:00", "18:00"),
        WorkedShift(2, "2026-11-02", "09:00", "15:30"),
    ]
    totals = totals_by_month(shifts)
    assert totals[1]["2026-10"] == MonthTotal(1, 390)
    assert totals[1]["2026-11"] == MonthTotal(2, 240 + 720)
    assert totals[2]["2026-11"] == MonthTotal(1, 390)


# ---------------------------------------------------------------------------
# 保存済みの勤務と合わせる（DB）
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = tmp_path / "hours.sqlite"
    monkeypatch.setenv(DB_PATH_ENV, str(path))
    connection = get_connection(path)
    initialize_database(connection)
    yield connection
    connection.close()


def add_staff(conn, code, start="09:00", end="15:30"):
    return repo.create_staff(conn, code, f"S{code}", 3, 3, standard_start_time=start,
                             standard_end_time=end, weekdays=list(range(7)))


def save_day(conn, work_date, staff_times):
    """勤務表を保存した状態を作る. staff_times = {staff_id: (start, end) or None(休み)}."""
    with conn:
        conn.execute("INSERT INTO schedule_days (work_date, status, updated_at) VALUES (?, 'DRAFT', '2026-11-01T00:00:00')",
                     (work_date,))
        for sid, times in staff_times.items():
            conn.execute(
                "INSERT INTO schedule_assignments (work_date, staff_id, is_working, start_time, end_time, locked, source, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 0, 'GENERATED', '2026-11-01T00:00:00')",
                (work_date, sid, int(times is not None), *(times or (None, None))),
            )


def test_new_draft_adds_to_hours_already_saved_this_month(conn):
    """11/1〜13 に保存済みの勤務があれば、11/14〜 の新しい勤務案と合わせて11月の合計にする."""
    sid = add_staff(conn, "1")
    for day in ("2026-11-02", "2026-11-03", "2026-11-04"):
        save_day(conn, day, {sid: ("09:00", "15:30")})
    save_day(conn, "2026-11-05", {sid: None})  # 休みは数えない
    period = period_dates("2026-11-14", 14)
    draft = [WorkedShift(sid, "2026-11-16", "09:00", "13:00"), WorkedShift(sid, "2026-11-17", "09:00", "15:30")]

    totals = monthly_totals(conn, period, draft)
    assert totals[sid]["2026-11"] == MonthTotal(5, 390 * 3 + 240 + 390)


def test_draft_replaces_saved_days_inside_the_period(conn):
    """期間内の日は、保存済みではなく新しい勤務案で数える（作り直したときに二重に数えない）."""
    sid = add_staff(conn, "1")
    save_day(conn, "2026-11-16", {sid: ("09:00", "15:30")})
    period = period_dates("2026-11-14", 14)
    totals = monthly_totals(conn, period, [WorkedShift(sid, "2026-11-17", "09:00", "13:00")])
    assert totals[sid]["2026-11"] == MonthTotal(1, 240)


def test_saved_schedule_counts_the_whole_month(conn):
    sid = add_staff(conn, "1")
    save_day(conn, "2026-10-30", {sid: ("09:00", "15:30")})
    save_day(conn, "2026-11-20", {sid: ("09:00", "15:30")})
    totals = monthly_totals(conn, period_dates("2026-10-31", 14))
    assert totals[sid]["2026-10"] == MonthTotal(1, 390)
    assert totals[sid]["2026-11"] == MonthTotal(1, 390)


# ---------------------------------------------------------------------------
# かんたん作成の③
# ---------------------------------------------------------------------------


def press(at, label):
    next(b for b in at.button if b.label == label).click().run()
    assert not at.exception


def test_step3_shows_month_totals_next_to_the_name(conn):
    sid = add_staff(conn, "1")
    add_staff(conn, "2")
    save_day(conn, "2026-11-02", {sid: ("09:00", "15:30")})  # 前の期間に保存済みの1日
    at = AppTest.from_file(APP, default_timeout=60).run()
    press(at, "勤務表をつくる")
    at.date_input[0].set_value(date(2026, 11, 14))
    press(at, "この期間で始める")
    for d in period_dates("2026-11-14", 14):
        at.number_input(key=f"gw_count_{d}").set_value(1)
    at.checkbox(key="gw_checked_preferences").set_value(True)
    press(at, "人数を保存して次へ")
    press(at, "勤務表をつくる")

    table = at.dataframe[0].value
    assert list(table.columns[:2]) == ["スタッフ", "11月の合計"]
    row = table[table["スタッフ"] == "S1"].iloc[0]
    worked = int(row["勤務日数"].rstrip("日"))
    assert row["11月の合計"] == f"{format_hours(390 * (worked + 1))}（{worked + 1}日）"
    assert row["勤務時間"] == format_hours(390 * worked)
    assert any("休憩は差し引いていません" in c.value for c in at.caption)
