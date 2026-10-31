"""祝日の表示のテスト（日付の「祝」・色分け・Excel の網掛け・印刷の見出し）."""

from datetime import date
from pathlib import Path

from streamlit.testing.v1 import AppTest

from src import jp_holidays
from src.database import get_connection, initialize_database
from src.ui_common import DB_PATH_ENV
from src.export_excel import SATURDAY_FILL, SUNDAY_FILL, weekday_fill
from src.jp_holidays import day_color, holiday_name, is_holiday
from src.period_utils import format_date_short
from src.ui_common import format_date_ja


def test_holiday_names_including_substitute_and_citizens_holidays():
    assert holiday_name("2026-11-03") == "文化の日"
    assert holiday_name("2026-11-23") == "勤労感謝の日"
    assert is_holiday("2026-05-06")  # 振替休日
    assert is_holiday("2026-09-22")  # 国民の休日
    assert holiday_name("2026-11-04") is None
    assert holiday_name("not-a-date") is None


def test_date_labels_mark_holidays():
    assert format_date_short("2026-11-03") == "11/3(火・祝)"
    assert format_date_short("2026-11-04") == "11/4(水)"
    assert format_date_ja("2026-11-23") == "11月23日(月・祝)"


def test_day_color():
    assert day_color("2026-11-07") == "blue"  # 土曜
    assert day_color("2026-11-08") == "red"  # 日曜
    assert day_color("2026-11-03") == "red"  # 祝日（火曜）
    assert day_color("2026-11-04") is None


def test_excel_fills_holidays_like_sundays():
    assert weekday_fill("2026-11-03") is SUNDAY_FILL
    assert weekday_fill("2026-11-07") is SATURDAY_FILL
    assert weekday_fill("2026-11-04") is None


def test_without_jpholiday_there_are_no_holidays(monkeypatch):
    """jpholiday が入っていない環境でも落ちない（祝日なしとして表示）."""
    monkeypatch.setattr(jp_holidays, "_jpholiday", None)
    jp_holidays._holiday_name.cache_clear()
    try:
        assert format_date_short("2026-11-03") == "11/3(火)"
        assert day_color("2026-11-03") is None
    finally:
        jp_holidays._holiday_name.cache_clear()


def test_print_headers_are_colored():
    from src.schedule_print import _day_class

    assert _day_class("2026-11-03") == ' class="day-holiday"'
    assert _day_class("2026-11-07") == ' class="day-sat"'
    assert _day_class("2026-11-04") == ""


def test_requirements_page_shows_holiday_name(tmp_path, monkeypatch):
    path = tmp_path / "holiday.sqlite"
    monkeypatch.setenv(DB_PATH_ENV, str(path))
    conn = get_connection(str(path))
    initialize_database(conn)
    conn.close()
    page = str(Path(__file__).resolve().parents[1] / "app_pages" / "03_requirements.py")
    at = AppTest.from_file(page, default_timeout=120)
    at.session_state["requirement_period_start"] = date(2026, 11, 1)
    at.session_state["requirement_period_days"] = 14
    at.run()
    assert not at.exception
    texts = [m.value for m in at.markdown]
    assert any("11/3(火・祝)" in t and "文化の日" in t and ":red[" in t for t in texts)
    assert any(":blue[11/7(土)]" in t for t in texts)
