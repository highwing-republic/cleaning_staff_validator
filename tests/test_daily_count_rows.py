"""勤務表の先頭に出す「出勤人数」「必要人数」の行のテスト.

前半は表の組み立て（画面非依存）、後半はかんたん作成の③④での表示。
"""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src import repositories as repo
from src.daily_count_rows import (
    EXCESS_STYLE,
    REQUIRED_LABEL,
    SHORTAGE_STYLE,
    SUMMARY_STYLE,
    WORKING_LABEL,
    DayCount,
    build_schedule_table,
    counts_from_generation,
    counts_from_validation,
    required_cell,
    table_styles,
    working_cell,
)
from src.database import get_connection, initialize_database
from src.models import (
    DailyGenerationResult,
    DailyRequirementInput,
    ScheduleGenerationResult,
    ScheduleDayValidationResult,
    SchedulePeriodValidationResult,
)
from src.period_utils import format_date_short, period_dates
from src.ui_common import DB_PATH_ENV

APP = str(Path(__file__).resolve().parents[1] / "app.py")
D1, D2, D3 = "2026-11-02", "2026-11-03", "2026-11-04"


# ---------------------------------------------------------------------------
# 表示文
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (DayCount(5, 5), "5人"),
        (DayCount(6, 5), "6人"),                    # 必要人数は最低人数なので、多いのは問題なし
        (DayCount(4, 5), "4人（1人不足）"),
        (DayCount(8, 5, maximum=7), "8人（1人多い）"),
        (DayCount(3, None), "3人"),                 # 必要人数が未設定
        (DayCount(None, 5), "未作成"),
        (DayCount(0, 2), "0人（2人不足）"),
    ],
)
def test_working_cell(count, expected):
    assert working_cell(count) == expected


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (DayCount(5, 5), "5人"),
        (DayCount(5, 5, maximum=7), "5〜7人"),
        (DayCount(5, 5, maximum=5), "5人"),
        (DayCount(5, None), "未設定"),
        (DayCount(5, 0), "0人"),
    ],
)
def test_required_cell(count, expected):
    assert required_cell(count) == expected


# ---------------------------------------------------------------------------
# 人数の取り出し
# ---------------------------------------------------------------------------


def test_counts_from_saved_schedule():
    validation = SchedulePeriodValidationResult(
        work_dates=[D1, D2, D3],
        days=[
            ScheduleDayValidationResult(
                work_date=D1, schedule_status="DRAFT", scheduled_staff=4,
                requirement=DailyRequirementInput(D1, 5, max_total_staff=7),
            ),
            ScheduleDayValidationResult(work_date=D2, schedule_status="FINALIZED", scheduled_staff=3),
            ScheduleDayValidationResult(
                work_date=D3, requirement=DailyRequirementInput(D3, 2),
            ),
        ],
    )
    counts = counts_from_validation(validation)
    assert counts[D1] == DayCount(4, 5, 7)
    assert counts[D2] == DayCount(3, None, None)
    assert counts[D3] == DayCount(None, 2, None)  # 勤務表が未作成の日


def test_counts_from_new_draft():
    result = ScheduleGenerationResult(
        work_dates=[D1, D2],
        days=[
            DailyGenerationResult(D1, "SHORTAGE", True, 4, required_total_staff=5, staff_shortage=1),
            DailyGenerationResult(D2, "REQUIREMENT_MISSING", False, 3),
        ],
    )
    counts = counts_from_generation(result, {D1: 6})
    assert counts[D1] == DayCount(4, 5, 6)
    assert counts[D2] == DayCount(3, None, None)


# ---------------------------------------------------------------------------
# 表と色
# ---------------------------------------------------------------------------


def staff_row(name, cells, days):
    return {"スタッフ": name, **{format_date_short(d): v for d, v in cells.items()}, "勤務日数": days}


def make_table():
    dates = [D1, D2]
    rows = [
        staff_row("Aさん", {D1: "09:00-15:30", D2: "休"}, 1),
        staff_row("Bさん", {D1: "09:00-15:30", D2: "09:00-15:30"}, 2),
    ]
    counts = {D1: DayCount(2, 3), D2: DayCount(1, 1)}
    return dates, counts, build_schedule_table(rows, dates, counts, format_date_short)


def test_count_rows_come_first():
    """スタッフが多くても、スクロールせずに人数が見えるよう先頭に置く."""
    _, _, table = make_table()
    assert list(table["スタッフ"]) == [WORKING_LABEL, REQUIRED_LABEL, "Aさん", "Bさん"]
    first = table.iloc[0]
    assert first[format_date_short(D1)] == "2人（1人不足）"
    assert first[format_date_short(D2)] == "1人"
    assert table.iloc[1][format_date_short(D1)] == "3人"


def test_work_days_column():
    _, _, table = make_table()
    assert list(table["勤務日数"]) == ["", "", "1日", "2日"]


def test_columns_keep_the_date_order():
    _, _, table = make_table()
    assert list(table.columns) == ["スタッフ", "11/2(月)", "11/3(火)", "勤務日数"]


def test_unknown_day_is_blank():
    dates = [D1]
    table = build_schedule_table([staff_row("Aさん", {D1: "休"}, 0)], dates, {}, format_date_short)
    assert table.iloc[0][format_date_short(D1)] == ""


def test_styles_mark_shortage_and_summary_rows():
    dates, counts, table = make_table()
    styles = table_styles(table, dates, counts, format_date_short)
    assert styles.iloc[0][format_date_short(D1)] == SHORTAGE_STYLE
    assert styles.iloc[0][format_date_short(D2)] == SUMMARY_STYLE
    assert styles.iloc[1][format_date_short(D1)] == SUMMARY_STYLE
    assert styles.iloc[2][format_date_short(D1)] == ""  # スタッフの行は色を付けない


def test_styles_mark_excess():
    dates = [D1]
    counts = {D1: DayCount(8, 5, maximum=7)}
    table = build_schedule_table([], dates, counts, format_date_short)
    styles = table_styles(table, dates, counts, format_date_short)
    assert styles.iloc[0][format_date_short(D1)] == EXCESS_STYLE


# ---------------------------------------------------------------------------
# かんたん作成の③④での表示
# ---------------------------------------------------------------------------

START = "2026-11-02"
DATES = period_dates(START, 10)


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "counts.sqlite"
    monkeypatch.setenv(DB_PATH_ENV, str(path))
    conn = get_connection(path)
    initialize_database(conn)
    for i in range(3):
        repo.create_staff(conn, str(i), f"スタッフ{i}", 3, 3,
                          standard_start_time="09:00", standard_end_time="15:30",
                          weekdays=list(range(7)), target_days_per_week=3)
    conn.close()
    return path


def press(at, label):
    next(b for b in at.button if b.label == label).click().run()
    assert not at.exception


def open_step3(required):
    at = AppTest.from_file(APP, default_timeout=60).run()
    press(at, "勤務表をつくる")
    at.date_input[0].set_value(date.fromisoformat(START))
    at.selectbox[0].set_value(10)
    press(at, "この期間で始める")
    for d in DATES:
        at.number_input(key=f"gw_count_{d}").set_value(required.get(d, 1))
    at.checkbox(key="gw_checked_preferences").set_value(True)
    press(at, "人数を保存して次へ")
    return at


def first_rows(at, index=0):
    table = at.dataframe[index].value
    return table.iloc[0], table.iloc[1]


def test_new_draft_shows_counts_and_shortage(db):
    """3人しかいない日に4人必要なら、出勤人数の行に不足が出る."""
    at = open_step3({DATES[0]: 4})
    press(at, "勤務表をつくる")
    working, required = first_rows(at)
    assert working["スタッフ"] == WORKING_LABEL
    assert required[format_date_short(DATES[0])] == "4人"
    assert "人不足" in working[format_date_short(DATES[0])]
    assert required[format_date_short(DATES[1])] == "1人"


def test_saved_schedule_shows_counts_matching_the_cells(db):
    at = open_step3({})
    press(at, "勤務表をつくる")
    press(at, "この案を保存して使う")
    table = at.dataframe[0].value
    staff = table.iloc[2:]
    for d in DATES:
        label = format_date_short(d)
        working = staff[label].str.contains(":").sum()
        assert table.iloc[0][label].startswith(f"{working}人")
        assert table.iloc[1][label] == "1人"
    assert any("出勤人数と必要人数" in c.value for c in at.caption)
