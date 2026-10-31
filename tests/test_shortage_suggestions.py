"""人が足りない日の候補（おすすめ）のテスト."""

import pytest

from src import guided_workflow as workflow
from src import repositories as repo
from src import services
from src.database import get_connection, initialize_database
from src.models import DailyRequirementInput, RoleRequirementInput, StaffDatePreferenceInput
from src.period_utils import period_dates
from src.shortage_suggestions import (
    CONSECUTIVE,
    NO_TIME,
    OFF_WEEKDAY,
    READY,
    collect_suggestions,
    consecutive_if_added,
    format_suggestion,
    saved_shortages,
    shortage_of_day,
)
from src.ui_common import DB_PATH_ENV

LEADER, CHECKER, CLEANER = 1, 2, 3
WEEKDAYS = [0, 1, 2, 3, 4]
SAT = "2026-11-07"
DATES = period_dates("2026-11-02", 7)  # 月〜日


@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = tmp_path / "suggest.sqlite"
    monkeypatch.setenv(DB_PATH_ENV, str(path))
    connection = get_connection(path)
    initialize_database(connection)
    workflow.initialize(connection)
    yield connection
    connection.close()


def add(conn, code, role_id=CLEANER, weekdays=WEEKDAYS, start="09:00", end="15:30", **kw):
    return repo.create_staff(conn, code, f"S{code}", role_id, 3, standard_start_time=start,
                             standard_end_time=end, weekdays=list(weekdays), **kw)


def require(conn, dates, total, roles=None):
    assert not services.save_period_requirements(
        conn, dates, [DailyRequirementInput(d, total) for d in dates],
        [RoleRequirementInput(d, r, n) for d in dates for r, n in (roles or {}).items()],
    )


def save_schedule(conn, dates):
    services.save_generated_schedule(conn, dates, services.generate_schedule(conn, dates))


def suggestions_for(conn, dates, work_date):
    shortages, working = saved_shortages(conn, dates)
    return collect_suggestions(conn, dates, shortages, working).get(work_date, [])


# ---------------------------------------------------------------------------
# 計算
# ---------------------------------------------------------------------------


def test_consecutive_if_added():
    worked = {"2026-11-02", "2026-11-03", "2026-11-05"}
    assert consecutive_if_added(worked, "2026-11-04") == 4
    assert consecutive_if_added(worked, "2026-11-07") == 1


def test_off_weekday_staff_is_suggested_and_absolute_off_is_not(conn):
    """土曜に1人足りない: 土曜がふだん休みの人を候補に. 「必ず休み」の人は出さない."""
    on_sat = add(conn, "1", weekdays=range(7))
    weekday_only = add(conn, "2")
    resting = add(conn, "3")
    repo.upsert_staff_date_preference(conn, StaffDatePreferenceInput(resting, SAT, absolute_off=True))
    require(conn, DATES, 2)
    save_schedule(conn, DATES)

    suggestions = suggestions_for(conn, DATES, SAT)
    assert [s.staff_id for s in suggestions] == [weekday_only]
    s = suggestions[0]
    assert s.kind == OFF_WEEKDAY and s.needs_extra_day and s.can_apply
    assert "ふだん休みの曜日" in s.reason and s.fills == "人数"
    assert on_sat


def test_only_people_who_can_fill_the_missing_role(conn):
    add(conn, "1", CLEANER, weekdays=range(7))
    add(conn, "2", CLEANER)  # 平日だけのクリーナーは、リーダー不足を埋められない
    leader = add(conn, "3", LEADER)
    checker = add(conn, "4", CHECKER, substitute_role_ids=[LEADER])
    require(conn, [SAT], 1, {LEADER: 1})
    save_schedule(conn, [SAT])

    suggestions = suggestions_for(conn, [SAT], SAT)
    assert [s.staff_id for s in suggestions] == [leader, checker]  # 自分の役割の人が先
    assert [s.fills for s in suggestions] == ["リーダー", "代わりにリーダー"]


def test_consecutive_limit_and_missing_time_come_last_and_cannot_be_applied(conn):
    add(conn, "1", weekdays=range(7))
    tired = add(conn, "2", max_consecutive_days=5)  # 月〜金で5連勤 → 土曜に出ると6連勤
    no_time = add(conn, "3", weekdays=range(7), start=None, end=None)
    easy = add(conn, "4", weekdays=[0])
    require(conn, DATES, 2)  # 平日は「1」と「tired」が毎日必要
    require(conn, [DATES[0]], 3)  # 月曜は「easy」も
    require(conn, [SAT], 4)
    save_schedule(conn, DATES)
    assert all(repo.get_schedule_assignment(conn, d, tired).is_working for d in DATES[:5])

    suggestions = suggestions_for(conn, DATES, SAT)
    kinds = {s.staff_id: s.kind for s in suggestions}
    assert kinds[easy] in (OFF_WEEKDAY, READY)
    assert kinds[no_time] == NO_TIME
    assert kinds[tired] == CONSECUTIVE
    assert [s.staff_id for s in suggestions][-1] == tired
    tired_s = next(s for s in suggestions if s.staff_id == tired)
    assert "6連勤（上限5）" in tired_s.reason and not tired_s.can_apply
    assert not next(s for s in suggestions if s.staff_id == no_time).can_apply


def test_fewer_hours_this_month_come_first(conn):
    add(conn, "1", weekdays=range(7))
    busy = add(conn, "2")
    free = add(conn, "3", weekdays=[0])
    require(conn, DATES, 1)
    require(conn, [SAT], 2)
    save_schedule(conn, DATES)
    # 「busy」は平日に何日も入っているはず
    suggestions = suggestions_for(conn, DATES, SAT)
    ids = [s.staff_id for s in suggestions]
    assert ids.index(free) < ids.index(busy)
    assert "今月" in format_suggestion(suggestions[0])


def test_no_shortage_no_suggestions(conn):
    add(conn, "1", weekdays=range(7))
    require(conn, DATES, 1)
    save_schedule(conn, DATES)
    shortages, _ = saved_shortages(conn, DATES)
    assert shortages == []


def test_shortage_of_day_counts_substitutes():
    from src.models import StaffDetail, StaffInput

    def detail(sid, role, subs=()):
        return StaffDetail(staff=StaffInput(sid, str(sid), f"S{sid}", role, 3), substitute_role_ids=tuple(subs))

    s = shortage_of_day(SAT, [detail(1, CHECKER, [LEADER]), detail(2, CLEANER)], 3, {LEADER: 1, CLEANER: 1})
    assert s.staff_shortage == 1 and s.role_shortages == {}


# ---------------------------------------------------------------------------
# 「この人を出勤にする」
# ---------------------------------------------------------------------------


def test_assign_off_weekday_staff_registers_extra_day_and_locks(conn):
    add(conn, "1", weekdays=range(7))
    weekday_only = add(conn, "2")
    require(conn, DATES, 2)
    save_schedule(conn, DATES)

    workflow.assign_suggestion(conn, DATES, weekday_only, SAT, True, workflow.input_fingerprint(conn, DATES))

    assignment = repo.get_schedule_assignment(conn, SAT, weekday_only)
    assert assignment.is_working and assignment.locked
    assert (assignment.start_time, assignment.end_time) == ("09:00", "15:30")
    assert repo.get_staff_date_preference(conn, weekday_only, SAT).available_extra
    assert SAT not in {s.work_date for s in saved_shortages(conn, DATES)[0]}


def test_assign_keeps_an_existing_note_on_the_preference(conn):
    add(conn, "1", weekdays=range(7))
    sid = add(conn, "2")
    repo.upsert_staff_date_preference(conn, StaffDatePreferenceInput(sid, SAT, note="午後だけ"))
    require(conn, DATES, 2)
    save_schedule(conn, DATES)
    workflow.assign_suggestion(conn, DATES, sid, SAT, True, workflow.input_fingerprint(conn, DATES))
    pref = repo.get_staff_date_preference(conn, sid, SAT)
    assert pref.available_extra and pref.note == "午後だけ"


def test_assign_refuses_when_something_changed_and_saves_nothing(conn):
    add(conn, "1", weekdays=range(7))
    sid = add(conn, "2")
    require(conn, DATES, 2)
    save_schedule(conn, DATES)
    stale = workflow.input_fingerprint(conn, DATES)
    add(conn, "3")  # 別の画面で変更があった
    with pytest.raises(workflow.WorkflowError):
        workflow.assign_suggestion(conn, DATES, sid, SAT, True, stale)
    assert repo.get_staff_date_preference(conn, sid, SAT) is None
    assert not repo.get_schedule_assignment(conn, SAT, sid).is_working


def test_assign_rolls_back_the_extra_day_when_the_consecutive_limit_blocks(conn):
    add(conn, "1", weekdays=range(7))
    tired = add(conn, "2", max_consecutive_days=5)
    require(conn, DATES, 2)
    save_schedule(conn, DATES)
    with pytest.raises(workflow.WorkflowError):
        workflow.assign_suggestion(conn, DATES, tired, SAT, True, workflow.input_fingerprint(conn, DATES))
    assert repo.get_staff_date_preference(conn, tired, SAT) is None


# ---------------------------------------------------------------------------
# かんたん作成③の画面
# ---------------------------------------------------------------------------


def test_step3_shows_candidates_and_assigns_with_the_button(conn):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    # かんたん作成は人数から役割を決める（1名→リーダー1、2名→リーダー1・チェッカー1）
    add(conn, "1", LEADER, weekdays=range(7))
    weekday_only = add(conn, "2", CHECKER)
    workflow.start_period(conn, DATES[0], 7)
    workflow.save_staff_counts(conn, DATES, {d: 2 if d == SAT else 1 for d in DATES},
                               workflow.review_fingerprint(conn, DATES, "requirements"))
    workflow.confirm_inputs(conn, DATES[0], 7)
    workflow.save_preview(conn, workflow.make_preview(conn, DATES))

    app = str(Path(__file__).resolve().parents[1] / "app.py")
    at = AppTest.from_file(app, default_timeout=60).run()
    next(b for b in at.button if b.label == "作成途中の勤務表を開く").click().run()
    next(b for b in at.button if b.label == "この勤務表を開く").click().run()
    assert at.session_state["_guided_step"] == 3
    assert any("足りない日に入ってもらえそうな人" in m.value for m in at.markdown)
    assert any("S2（チェッカー）：ふだん休みの曜日" in m.value for m in at.markdown)

    at.button(key=f"gw_suggest_{weekday_only}_{SAT}").click().run()
    assert not at.exception
    assert any("S2さんを11/7(土)の出勤にしました" in s.value for s in at.success)
    assert repo.get_schedule_assignment(conn, SAT, weekday_only).is_working
    assert not any("足りない日に入ってもらえそうな人" in m.value for m in at.markdown)  # 不足が解消した
