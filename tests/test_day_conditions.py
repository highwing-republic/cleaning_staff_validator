"""通常条件＋勤務希望から実効条件を求める解決ロジックのテスト.

指示書のR01〜R10に対応するテストには対応番号をコメントで示す。
この関数はDB・Streamlitに依存せず、将来Solverが結果を使う。
"""

import pytest

from src.constants import (
    TIME_STATUS_INVALID,
    TIME_STATUS_NOT_APPLICABLE,
    TIME_STATUS_OK,
    TIME_STATUS_UNSET,
)
from src.day_conditions import resolve_period_conditions, resolve_staff_day_condition
from src.models import StaffDatePreferenceInput, StaffDetail, StaffInput
from src.period_utils import period_dates
from src.validation import (
    PREFERENCE_ABSOLUTE_OFF_CONFLICT,
    validate_staff_date_preference,
)

# 2026-10-20(火) 〜 2026-11-02(月)
TUESDAY = "2026-10-20"      # weekday 1
WEDNESDAY = "2026-10-21"    # weekday 2
SUNDAY = "2026-10-25"       # weekday 6

# 通常は月(0)火(1)木(3)金(4)土(5)勤務。水(2)日(6)は通常休み
DEFAULT_WEEKDAYS = (0, 1, 3, 4, 5)


def make_staff(
    weekdays=DEFAULT_WEEKDAYS,
    standard_start_time="09:00",
    standard_end_time="15:30",
    staff_id=1,
) -> StaffDetail:
    return StaffDetail(
        staff=StaffInput(
            staff_id=staff_id,
            employee_code=f"{staff_id:04d}",
            staff_name="Aさん",
            role_id=3,
            skill_level=3,
            department="清掃",
            active=True,
            standard_start_time=standard_start_time,
            standard_end_time=standard_end_time,
        ),
        weekdays=weekdays,
    )


def pref(work_date, **kwargs) -> StaffDatePreferenceInput:
    return StaffDatePreferenceInput(staff_id=1, work_date=work_date, **kwargs)


# ---------------------------------------------------------------------------
# can_work の判定
# ---------------------------------------------------------------------------


def test_r01_normal_weekday_without_preference():
    """R01: 通常勤務曜日・希望なし → 勤務可能・通常時間."""
    c = resolve_staff_day_condition(make_staff(), TUESDAY)
    assert c.base_available is True
    assert c.can_work is True
    assert (c.effective_start_time, c.effective_end_time) == ("09:00", "15:30")
    assert c.time_status == TIME_STATUS_OK
    assert c.has_preference is False


def test_r02_normal_off_weekday_without_preference():
    """R02: 通常休み曜日・希望なし → 勤務不可."""
    c = resolve_staff_day_condition(make_staff(), WEDNESDAY)
    assert c.base_available is False
    assert c.can_work is False
    assert c.time_status == TIME_STATUS_NOT_APPLICABLE


def test_r03_available_extra_on_normal_off_weekday():
    """R03: 通常休み曜日 + AVAILABLE_EXTRA → 勤務可能."""
    c = resolve_staff_day_condition(
        make_staff(), WEDNESDAY, pref(WEDNESDAY, available_extra=True)
    )
    assert c.base_available is False
    assert c.can_work is True
    assert c.available_extra is True
    assert (c.effective_start_time, c.effective_end_time) == ("09:00", "15:30")


def test_r04_absolute_off_on_normal_weekday():
    """R04: 通常勤務曜日 + ABSOLUTE_OFF → 勤務不可."""
    c = resolve_staff_day_condition(
        make_staff(), TUESDAY, pref(TUESDAY, absolute_off=True)
    )
    assert c.base_available is True
    assert c.can_work is False
    assert c.absolute_off is True
    assert c.effective_start_time is None
    assert c.effective_end_time is None


def test_r10_absolute_off_takes_priority_over_available_extra():
    """R10: ABSOLUTE_OFF が最優先. AVAILABLE_EXTRA との併用は検証エラー."""
    conflicting = pref(SUNDAY, absolute_off=True, available_extra=True)
    # 仮に両方立っていても勤務不可側が優先される
    c = resolve_staff_day_condition(make_staff(), SUNDAY, conflicting)
    assert c.can_work is False
    # 入力としては矛盾なので保存前に弾く
    codes = [e.code for e in validate_staff_date_preference(conflicting, make_staff())]
    assert PREFERENCE_ABSOLUTE_OFF_CONFLICT in codes


def test_available_extra_on_normal_weekday_is_harmless():
    """通常勤務曜日に AVAILABLE_EXTRA があっても勤務可能のまま（二重指定でも壊れない）."""
    c = resolve_staff_day_condition(
        make_staff(), TUESDAY, pref(TUESDAY, available_extra=True)
    )
    assert c.can_work is True


def test_staff_without_weekday_pattern_cannot_work_by_default():
    """通常勤務曜日が未設定のスタッフは、AVAILABLE_EXTRAがない限り勤務不可."""
    staff = make_staff(weekdays=())
    assert resolve_staff_day_condition(staff, TUESDAY).can_work is False
    assert (
        resolve_staff_day_condition(
            staff, TUESDAY, pref(TUESDAY, available_extra=True)
        ).can_work
        is True
    )


# ---------------------------------------------------------------------------
# 実効勤務時間
# ---------------------------------------------------------------------------


def test_r05_early_leave_replaces_end_time():
    """R05: 09:00-15:30 + 早上がり13:00 → 09:00-13:00."""
    c = resolve_staff_day_condition(
        make_staff(), TUESDAY, pref(TUESDAY, override_end_time="13:00")
    )
    assert (c.effective_start_time, c.effective_end_time) == ("09:00", "13:00")
    assert c.time_status == TIME_STATUS_OK
    assert c.start_overridden is False
    assert c.end_overridden is True


def test_r06_late_start_replaces_start_time():
    """R06: 09:00-15:30 + 遅出10:00 → 10:00-15:30."""
    c = resolve_staff_day_condition(
        make_staff(), TUESDAY, pref(TUESDAY, override_start_time="10:00")
    )
    assert (c.effective_start_time, c.effective_end_time) == ("10:00", "15:30")
    assert c.start_overridden is True
    assert c.end_overridden is False


def test_r07_both_overrides():
    """R07: 遅出10:00 + 早上がり13:00 → 10:00-13:00."""
    c = resolve_staff_day_condition(
        make_staff(),
        TUESDAY,
        pref(TUESDAY, override_start_time="10:00", override_end_time="13:00"),
    )
    assert (c.effective_start_time, c.effective_end_time) == ("10:00", "13:00")
    assert c.time_status == TIME_STATUS_OK


def test_r09_early_leave_does_not_force_working():
    """R09: 早上がりだけでは出勤を強制しない（通常休み曜日なら勤務不可のまま）."""
    c = resolve_staff_day_condition(
        make_staff(), WEDNESDAY, pref(WEDNESDAY, override_end_time="13:00")
    )
    assert c.can_work is False
    assert c.effective_end_time is None


def test_late_start_does_not_force_working():
    c = resolve_staff_day_condition(
        make_staff(), WEDNESDAY, pref(WEDNESDAY, override_start_time="10:00")
    )
    assert c.can_work is False


def test_override_time_is_normalized():
    c = resolve_staff_day_condition(
        make_staff(), TUESDAY, pref(TUESDAY, override_start_time="9:30")
    )
    assert c.effective_start_time == "09:30"


def test_time_status_invalid_when_effective_times_reversed():
    """実効時間が 開始 >= 終了 になる場合は確定させず INVALID にする."""
    c = resolve_staff_day_condition(
        make_staff(), TUESDAY, pref(TUESDAY, override_start_time="16:00")
    )
    assert c.time_status == TIME_STATUS_INVALID


def test_time_status_unset_when_standard_time_missing():
    """通常勤務時間が未設定なら確定できない（勝手な時刻を補完しない）."""
    staff = make_staff(standard_start_time=None, standard_end_time=None)
    c = resolve_staff_day_condition(staff, TUESDAY)
    assert c.can_work is True
    assert c.time_status == TIME_STATUS_UNSET
    assert c.effective_start_time is None
    assert c.effective_end_time is None


def test_partial_override_with_missing_standard_time():
    """早上がりだけ分かっていても、開始が不明なら確定しない."""
    staff = make_staff(standard_start_time=None, standard_end_time=None)
    c = resolve_staff_day_condition(
        staff, TUESDAY, pref(TUESDAY, override_end_time="13:00")
    )
    assert c.time_status == TIME_STATUS_UNSET
    assert c.effective_start_time is None
    assert c.effective_end_time == "13:00"


def test_both_overrides_resolve_even_without_standard_time():
    """両方overrideされていれば通常勤務時間が未設定でも確定できる."""
    staff = make_staff(standard_start_time=None, standard_end_time=None)
    c = resolve_staff_day_condition(
        staff,
        TUESDAY,
        pref(TUESDAY, override_start_time="10:00", override_end_time="13:00"),
    )
    assert (c.effective_start_time, c.effective_end_time) == ("10:00", "13:00")
    assert c.time_status == TIME_STATUS_OK


# ---------------------------------------------------------------------------
# PREFER_OFF / note
# ---------------------------------------------------------------------------


def test_r08_prefer_off_does_not_change_can_work():
    """R08: できれば休み → can_work は変わらず prefer_off=True."""
    c = resolve_staff_day_condition(
        make_staff(), TUESDAY, pref(TUESDAY, prefer_off=True)
    )
    assert c.can_work is True
    assert c.prefer_off is True
    assert (c.effective_start_time, c.effective_end_time) == ("09:00", "15:30")


def test_prefer_off_combined_with_early_leave():
    """「できれば休み。勤務するなら13時まで」を1日で表せること."""
    c = resolve_staff_day_condition(
        make_staff(), TUESDAY, pref(TUESDAY, prefer_off=True, override_end_time="13:00")
    )
    assert c.prefer_off is True
    assert c.can_work is True
    assert c.effective_end_time == "13:00"


def test_note_is_carried_and_trimmed():
    c = resolve_staff_day_condition(
        make_staff(), TUESDAY, pref(TUESDAY, note="  通院  ")
    )
    assert c.note == "通院"


def test_blank_note_becomes_none():
    c = resolve_staff_day_condition(make_staff(), TUESDAY, pref(TUESDAY, note="   "))
    assert c.note is None


def test_note_only_preference_does_not_change_conditions():
    """備考はSolverの条件に使わない."""
    c = resolve_staff_day_condition(make_staff(), TUESDAY, pref(TUESDAY, note="通院"))
    assert c.can_work is True
    assert c.prefer_off is False
    assert (c.effective_start_time, c.effective_end_time) == ("09:00", "15:30")


# ---------------------------------------------------------------------------
# その他
# ---------------------------------------------------------------------------


def test_invalid_work_date_raises():
    with pytest.raises(ValueError):
        resolve_staff_day_condition(make_staff(), "2026-02-30")


def test_resolve_period_conditions_covers_every_day():
    dates = period_dates("2026-10-20", 14)
    preferences = {
        "2026-10-22": pref("2026-10-22", absolute_off=True),
        "2026-11-01": pref("2026-11-01", available_extra=True),
    }
    conditions = resolve_period_conditions(make_staff(), dates, preferences)

    assert [c.work_date for c in conditions] == dates
    by_date = {c.work_date: c for c in conditions}
    assert by_date["2026-10-22"].can_work is False
    assert by_date["2026-11-01"].can_work is True   # 日曜だがAVAILABLE_EXTRA
    assert by_date["2026-10-20"].has_preference is False


def test_resolve_period_conditions_without_preferences():
    dates = period_dates("2026-10-20", 14)
    conditions = resolve_period_conditions(make_staff(), dates)
    assert all(c.has_preference is False for c in conditions)
    assert sum(1 for c in conditions if c.can_work) == 10   # 水・日が通常休み
