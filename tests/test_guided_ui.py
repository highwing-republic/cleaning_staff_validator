"""利用者の操作で保存・中断再開・手直し・確定まで進めることを検証する。"""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src import guided_workflow as workflow, repositories as repo, services
from src.database import get_connection, initialize_database
from src.period_utils import period_dates
from src.ui_common import DB_PATH_ENV

APP = str(Path(__file__).resolve().parents[1] / "app.py")
START = "2026-11-02"
DATES = period_dates(START, 10)


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "guided.sqlite"
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
    return at


def start(at):
    press(at, "勤務表をつくる")
    at.date_input[0].set_value(date.fromisoformat(START))
    at.selectbox[0].set_value(10)
    press(at, "この期間で始める")
    return at


def inputs(at):
    for d in DATES:
        at.number_input(key=f"gw_count_{d}").set_value(1)
    at.checkbox(key="gw_checked_preferences").set_value(True)
    press(at, "人数を保存して次へ")
    return at


def saved(at):
    press(at, "勤務表をつくる")
    press(at, "この案を保存して使う")
    return at


def test_guided_create_edit_finalize_and_print(db):
    at = AppTest.from_file(APP, default_timeout=45).run()
    assert not at.exception
    start(at)
    inputs(at)
    saved(at)
    conn = get_connection(db)
    current = services.get_current_schedule(conn, DATES)
    assert len(current.existing_dates) == 10
    sid = next(iter(current.days[0].assignments))
    at.selectbox(key="gw_edit_staff").set_value(sid).run()
    at.selectbox(key="gw_edit_date").set_value(DATES[0]).run()
    next(r for r in at.radio if r.label == "この日の勤務").set_value("休み")
    press(at, "この変更を保存")
    assert repo.get_schedule_assignment(conn, DATES[0], sid).locked
    assert not repo.get_schedule_assignment(conn, DATES[0], sid).is_working
    press(at, "保存した勤務表で次へ")
    next(c for c in at.checkbox if c.label == "勤務表と不足などの注意事項を確認しました").set_value(True)
    press(at, "この期間を配布用に確定")
    assert len(services.get_current_schedule(conn, DATES).finalized_dates) == 10
    assert any("確定済み" in s.value for s in at.success)
    assert len(at.get("iframe")) == 1
    conn.close()


def test_saved_progress_resumes_in_a_new_browser_session(db):
    first = AppTest.from_file(APP, default_timeout=45).run()
    inputs(start(first))
    second = AppTest.from_file(APP, default_timeout=45).run()
    press(second, "作成途中の勤務表を開く")
    press(second, "この勤務表を開く")
    assert second.session_state["_guided_period"] == (START, 10)
    assert second.session_state["_guided_step"] == 3
    assert any(b.label == "勤務表をつくる" for b in second.button)


def test_no_preference_confirmation_does_not_advance(db):
    at = start(AppTest.from_file(APP, default_timeout=45).run())
    press(at, "人数を保存して次へ")
    assert at.session_state["_guided_step"] == 2
    assert any("チェック" in w.value for w in at.warning)


def test_guided_stale_preview_shows_recovery_and_does_not_save(db):
    at = inputs(start(AppTest.from_file(APP, default_timeout=45).run()))
    press(at, "勤務表をつくる")
    conn = get_connection(db)
    old = services.get_period_requirements(conn, DATES)[0].requirement
    from dataclasses import replace
    repo.save_daily_requirements(conn, [replace(old, required_total_staff=2)])
    press(at, "この案を保存して使う")
    assert any("古い案" in e.value for e in at.error)
    assert services.get_current_schedule(conn, DATES).existing_dates == []
    press(at, "最新の内容を読み直す")
    assert not any(b.label == "この案を保存して使う" for b in at.button)
    conn.close()


def test_completed_period_can_be_reopened_for_print(db):
    conn = get_connection(db)
    workflow.initialize(conn)
    workflow.start_period(conn, START, 10)
    workflow.save_staff_counts(conn, DATES, {d: 1 for d in DATES}, workflow.review_fingerprint(conn, DATES, "requirements"))
    workflow.save_preview(conn, workflow.make_preview(conn, DATES))
    workflow.finalize_period(conn, DATES, workflow.input_fingerprint(conn, DATES), True)
    conn.close()
    at = AppTest.from_file(APP, default_timeout=45).run()
    press(at, "完成した勤務表を見る・印刷する")
    press(at, "この勤務表を開く")
    assert at.session_state["_guided_step"] == 4
    assert len(at.get("iframe")) == 1


def test_period_follows_guided_flow_to_detailed_pages(db):
    at = start(AppTest.from_file(APP, default_timeout=45).run())
    at.switch_page("app_pages/03_requirements.py").run()
    assert not at.exception
    assert at.date_input[0].value == date.fromisoformat(START)
    assert at.selectbox(key="requirement_period_days").value == 10
    at.date_input[0].set_value(date(2026, 11, 5)).run()
    at.switch_page("app_pages/08_output.py").run()
    assert not at.exception
    assert at.date_input[0].value == date(2026, 11, 5)
    at.switch_page("app_pages/03_requirements.py").run()
    assert not at.exception
    assert at.date_input[0].value == date(2026, 11, 5)


def test_step4_does_not_offer_distribution_before_finalizing(db):
    """④で確定する前は、配布用の印刷・Excelを出さない（下書きはチェックを入れたときだけ）."""
    at = AppTest.from_file(APP, default_timeout=45).run()
    start(at)
    inputs(at)
    saved(at)
    press(at, "保存した勤務表で次へ")
    assert at.session_state["_guided_step"] == 4
    assert not at.get("iframe")
    assert not any(b.label == "勤務表Excelをダウンロード" for b in at.download_button)
    assert any("まだ配布できません" in w.value and "この期間を配布用に確定" in w.value for w in at.warning)
    at.checkbox(key="gw_output_draft_ok").check().run()
    assert len(at.get("iframe")) == 1
    assert any(b.label == "下書きのExcelをダウンロード" for b in at.download_button)
