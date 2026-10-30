"""勤務希望の入力部品（普段と違う日だけ登録する）のテスト.

前半は保存する値の組み立て（画面非依存）、後半はかんたん作成の②での操作。
"""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src import repositories as repo
from src.database import get_connection, initialize_database
from src.models import StaffDatePreferenceInput
from src.period_utils import period_dates
from src.models import StaffDayCondition, StaffDetail, StaffInput
from src.preference_entry import (
    OFF_ABSOLUTE,
    OFF_NONE,
    OFF_PREFER,
    build_preference,
    describe_normal_day,
    off_choice_of,
    save_button_label,
    summarize_preference,
)
from src.ui_common import DB_PATH_ENV

APP = str(Path(__file__).resolve().parents[1] / "app.py")
START = "2026-11-02"          # 月曜
DATES = period_dates(START, 10)
DAY = DATES[1]                # 11/03(火)
SUNDAY = "2026-11-08"


def build(off=OFF_NONE, extra=False, start="09:00", end="15:30", note="", existing=None,
          standard=("09:00", "15:30")):
    return build_preference(
        1, DAY, off, extra, start, end, note,
        standard_start=standard[0], standard_end=standard[1], existing=existing,
    )


# ---------------------------------------------------------------------------
# 保存する値の組み立て
# ---------------------------------------------------------------------------


def test_nothing_changed_is_an_empty_preference():
    """何も変えなければ普段どおり（保存すると行が消える）."""
    assert build().is_empty


def test_absolute_off():
    pref = build(off=OFF_ABSOLUTE)
    assert (pref.absolute_off, pref.prefer_off) == (True, False)
    assert (pref.override_start_time, pref.override_end_time) == (None, None)


def test_absolute_off_drops_times_and_extra():
    """必ず休みの日は時刻と「勤務できる」を持たない（両立しないため）."""
    pref = build(off=OFF_ABSOLUTE, extra=True, start="10:00", end="13:00")
    assert pref.available_extra is False
    assert (pref.override_start_time, pref.override_end_time) == (None, None)


def test_absolute_off_keeps_an_existing_prefer_off():
    """絶対休み＋できれば休みの併用は仕様上ありうるので、選び直しても消さない."""
    existing = StaffDatePreferenceInput(1, DAY, absolute_off=True, prefer_off=True)
    pref = build(off=OFF_ABSOLUTE, existing=existing)
    assert (pref.absolute_off, pref.prefer_off) == (True, True)


def test_prefer_off():
    pref = build(off=OFF_PREFER)
    assert (pref.absolute_off, pref.prefer_off) == (False, True)


def test_early_leave_only_stores_the_changed_end():
    pref = build(end="13:00")
    assert (pref.override_start_time, pref.override_end_time) == (None, "13:00")


def test_late_start_only_stores_the_changed_start():
    pref = build(start="10:00")
    assert (pref.override_start_time, pref.override_end_time) == ("10:00", None)


def test_prefer_off_with_early_leave():
    """「できれば休み。出るなら13時まで」を1日で登録できる."""
    pref = build(off=OFF_PREFER, end="13:00")
    assert pref.prefer_off is True
    assert pref.override_end_time == "13:00"


def test_times_without_a_standard_time_are_stored_as_chosen():
    pref = build(start="09:00", end="13:00", standard=(None, None))
    assert (pref.override_start_time, pref.override_end_time) == ("09:00", "13:00")


def test_unset_times_without_a_standard_time_stay_empty():
    assert build(start=None, end=None, standard=(None, None)).is_empty


def test_available_extra():
    assert build(extra=True).available_extra is True


def test_note_only():
    pref = build(note="  通院  ")
    assert pref.note == "通院"
    assert not pref.absolute_off and not pref.prefer_off


@pytest.mark.parametrize(
    ("pref", "expected"),
    [
        (StaffDatePreferenceInput(1, DAY, absolute_off=True), "必ず休み"),
        (StaffDatePreferenceInput(1, DAY, prefer_off=True, override_end_time="13:00"),
         "できれば休み・13:00まで（早上がり）"),
        (StaffDatePreferenceInput(1, DAY, override_start_time="10:00"), "10:00から（遅出）"),
        (StaffDatePreferenceInput(1, DAY, override_start_time="10:00", override_end_time="13:00"),
         "10:00-13:00"),
        (StaffDatePreferenceInput(1, DAY, available_extra=True), "普段休みの日に勤務できる"),
        (StaffDatePreferenceInput(1, DAY, note="通院"), "メモのみ"),
    ],
)
def test_summary(pref, expected):
    assert summarize_preference(pref) == expected


def test_off_choice_of_saved_preference():
    assert off_choice_of(None) == OFF_NONE
    assert off_choice_of(StaffDatePreferenceInput(1, DAY, prefer_off=True)) == OFF_PREFER
    both = StaffDatePreferenceInput(1, DAY, absolute_off=True, prefer_off=True)
    assert off_choice_of(both) == OFF_ABSOLUTE


# ---------------------------------------------------------------------------
# かんたん作成の②での操作
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "entry.sqlite"
    monkeypatch.setenv(DB_PATH_ENV, str(path))
    conn = get_connection(path)
    initialize_database(conn)
    ids = {
        "full": repo.create_staff(conn, "1", "常勤Aさん", 3, 3,
                                  standard_start_time="09:00", standard_end_time="15:30",
                                  weekdays=list(range(7))),
        # 土日が普段休み
        "weekday": repo.create_staff(conn, "2", "平日Bさん", 3, 3,
                                     standard_start_time="09:00", standard_end_time="13:00",
                                     weekdays=[0, 1, 2, 3, 4]),
        "no_time": repo.create_staff(conn, "3", "時間未設定Cさん", 3, 3,
                                     weekdays=list(range(7))),
    }
    conn.close()
    return path, ids


def press(at, label=None, key=None):
    button = at.button(key=key) if key else next(b for b in at.button if b.label == label)
    button.click().run()
    assert not at.exception
    return at


def open_step2():
    at = AppTest.from_file(APP, default_timeout=45).run()
    assert not at.exception
    press(at, "勤務表をつくる")
    at.date_input[0].set_value(date.fromisoformat(START))
    at.selectbox[0].set_value(10)
    press(at, "この期間で始める")
    return at


def choose(at, staff_id, work_date):
    at.selectbox(key="gw_pref_staff").set_value(staff_id).run()
    at.selectbox(key="gw_pref_date").set_value(work_date).run()
    assert not at.exception
    return f"gw_pref_{staff_id}_{work_date}"


def saved(path, staff_id, work_date):
    conn = get_connection(path)
    pref = repo.get_staff_date_preference(conn, staff_id, work_date)
    conn.close()
    return pref


def test_step2_has_no_per_day_grid(db):
    """14日分の入力欄を並べず、スタッフと日付を選んで1日ずつ登録する."""
    at = open_step2()
    assert at.selectbox(key="gw_pref_staff")
    assert at.selectbox(key="gw_pref_date")
    assert not [t for t in at.text_input if t.label.startswith("変更後の")]


def test_times_default_to_the_staffs_normal_hours(db):
    path, ids = db
    at = open_step2()
    prefix = choose(at, ids["full"], DAY)
    assert at.selectbox(key=f"{prefix}_start_hour").value == 9
    assert at.selectbox(key=f"{prefix}_start_minute").value == "00"
    assert at.selectbox(key=f"{prefix}_end_hour").value == 15
    assert at.selectbox(key=f"{prefix}_end_minute").value == "30"
    assert any("09:00-15:30" in i.value for i in at.info)


def test_register_absolute_off(db):
    path, ids = db
    at = open_step2()
    prefix = choose(at, ids["full"], DAY)
    at.radio(key=f"{prefix}_off").set_value(OFF_ABSOLUTE).run()
    press(at, key="gw_pref_save")

    pref = saved(path, ids["full"], DAY)
    assert pref.absolute_off is True
    assert any("希望を保存しました" in s.value for s in at.success)


def test_absolute_off_hides_the_time_fields(db):
    path, ids = db
    at = open_step2()
    prefix = choose(at, ids["full"], DAY)
    at.radio(key=f"{prefix}_off").set_value(OFF_ABSOLUTE).run()
    keys = {s.key for s in at.selectbox}
    assert f"{prefix}_start_hour" not in keys
    assert f"{prefix}_end_hour" not in keys


def test_register_early_leave_with_the_dropdown(db):
    path, ids = db
    at = open_step2()
    prefix = choose(at, ids["full"], DAY)
    at.selectbox(key=f"{prefix}_end_hour").set_value(13).run()
    at.selectbox(key=f"{prefix}_end_minute").set_value("00").run()
    press(at, key="gw_pref_save")

    pref = saved(path, ids["full"], DAY)
    assert (pref.override_start_time, pref.override_end_time) == (None, "13:00")


def test_register_extra_day_on_a_normally_off_weekday(db):
    path, ids = db
    at = open_step2()
    prefix = choose(at, ids["weekday"], SUNDAY)
    assert any("普段は休みの曜日" in i.value for i in at.info)
    at.checkbox(key=f"{prefix}_extra").check().run()
    press(at, key="gw_pref_save")
    assert saved(path, ids["weekday"], SUNDAY).available_extra is True


def test_extra_checkbox_is_not_shown_on_a_normal_work_day(db):
    path, ids = db
    at = open_step2()
    prefix = choose(at, ids["weekday"], DAY)
    assert f"{prefix}_extra" not in {c.key for c in at.checkbox}


def test_registered_list_and_back_to_normal(db):
    path, ids = db
    conn = get_connection(path)
    repo.save_staff_period_preferences(
        conn, ids["full"], DATES,
        [StaffDatePreferenceInput(ids["full"], DAY, prefer_off=True, override_end_time="13:00")],
    )
    conn.close()

    at = open_step2()
    texts = [m.value for m in at.markdown]
    assert "できれば休み・13:00まで（早上がり）" in texts
    press(at, key=f"gw_pref_reset_{ids['full']}_{DAY}")
    assert saved(path, ids["full"], DAY) is None
    assert any("通常に戻しました" in s.value for s in at.success)


def test_edit_button_opens_that_day(db):
    path, ids = db
    conn = get_connection(path)
    repo.save_staff_period_preferences(
        conn, ids["weekday"], DATES,
        [StaffDatePreferenceInput(ids["weekday"], DATES[3], absolute_off=True)],
    )
    conn.close()

    at = open_step2()
    press(at, key=f"gw_pref_edit_{ids['weekday']}_{DATES[3]}")
    assert at.selectbox(key="gw_pref_staff").value == ids["weekday"]
    assert at.selectbox(key="gw_pref_date").value == DATES[3]
    prefix = f"gw_pref_{ids['weekday']}_{DATES[3]}"
    assert at.radio(key=f"{prefix}_off").value == OFF_ABSOLUTE


def test_saving_without_changes_returns_the_day_to_normal(db):
    path, ids = db
    conn = get_connection(path)
    repo.save_staff_period_preferences(
        conn, ids["full"], DATES,
        [StaffDatePreferenceInput(ids["full"], DAY, note="通院")],
    )
    conn.close()

    at = open_step2()
    prefix = choose(at, ids["full"], DAY)
    at.text_input(key=f"{prefix}_note").input("").run()
    press(at, key="gw_pref_save")
    assert saved(path, ids["full"], DAY) is None


def test_staff_without_normal_hours_gets_a_warning(db):
    path, ids = db
    at = open_step2()
    prefix = choose(at, ids["no_time"], DAY)
    assert any("通常勤務時間が未設定" in w.value for w in at.warning)
    assert at.selectbox(key=f"{prefix}_start_hour").value is None


def test_switching_day_does_not_carry_unsaved_input(db):
    path, ids = db
    at = open_step2()
    prefix = choose(at, ids["full"], DAY)
    at.radio(key=f"{prefix}_off").set_value(OFF_ABSOLUTE).run()
    other = choose(at, ids["full"], DATES[2])
    assert at.radio(key=f"{other}_off").value == OFF_NONE
    press(at, key="gw_pref_save")
    assert saved(path, ids["full"], DATES[2]) is None
    assert saved(path, ids["full"], DAY) is None


# ---------------------------------------------------------------------------
# 「この日」ではなく具体的な日付で書く
# ---------------------------------------------------------------------------


def _detail(start="09:00", end="19:00"):
    return StaffDetail(staff=StaffInput(1, "0001", "太郎", 3, 3, "清掃", True, start, end))


def _condition(work_date, base_available):
    return StaffDayCondition(staff_id=1, work_date=work_date, base_available=base_available,
                             can_work=base_available)


def test_describe_a_normally_off_day_with_the_date():
    text = describe_normal_day(_condition("2026-11-01", False), _detail())
    assert text == "太郎の 11/1(日) は、普段は休みの曜日です。"


def test_describe_a_work_day_with_the_date_and_hours():
    text = describe_normal_day(_condition("2026-11-02", True), _detail())
    assert text == "太郎の 11/2(月) は、普段どおりなら 09:00-19:00 の勤務です。"


def test_describe_a_work_day_without_hours():
    text = describe_normal_day(_condition("2026-11-02", True), _detail(None, None))
    assert "11/2(月)" in text and "未設定" in text


def test_save_button_names_the_date():
    assert save_button_label("2026-11-01") == "11/1(日)の希望を保存"


def test_entry_form_names_the_selected_date_instead_of_this_day(db):
    """選んだ日付が説明・選択肢・ボタンに書かれ、「この日」という書き方をしない."""
    path, ids = db
    at = open_step2()
    prefix = choose(at, ids["weekday"], SUNDAY)

    assert any("11/8(日) は、普段は休みの曜日です" in i.value for i in at.info)
    assert at.radio(key=f"{prefix}_off").label == "11/8(日)の休み"
    assert at.checkbox(key=f"{prefix}_extra").label.startswith("11/8(日)は勤務できる")
    assert at.button(key="gw_pref_save").label == "11/8(日)の希望を保存"

    shown = " ".join(
        [i.value for i in at.info] + [c.value for c in at.caption]
        + [r.label for r in at.radio] + [c.label for c in at.checkbox] + [b.label for b in at.button]
    )
    assert "この日" not in shown
    assert "その日" not in shown
