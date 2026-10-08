"""期間別勤務希望の表示用文字列のテスト."""

import pytest

from src.day_conditions import resolve_staff_day_condition
from src.models import StaffDatePreferenceInput, StaffDetail, StaffInput
from src.preference_display import (
    NORMAL_LABEL,
    NOTE_MARK,
    format_base_availability,
    format_date_short,
    format_day_condition,
    format_effective_time,
)

TUESDAY = "2026-10-20"      # 通常勤務曜日
WEDNESDAY = "2026-10-21"    # 通常休み曜日


def make_staff(standard_start_time="09:00", standard_end_time="15:30") -> StaffDetail:
    return StaffDetail(
        staff=StaffInput(
            1, "0001", "Aさん", 3, 3, "清掃", True,
            standard_start_time, standard_end_time,
        ),
        weekdays=(0, 1, 3, 4, 5),
    )


def condition(work_date, staff=None, **kwargs):
    preference = (
        StaffDatePreferenceInput(staff_id=1, work_date=work_date, **kwargs) if kwargs else None
    )
    return resolve_staff_day_condition(staff or make_staff(), work_date, preference)


class TestFormatDateShort:
    @pytest.mark.parametrize(
        ("work_date", "expected"),
        [
            ("2026-10-20", "10/20(火)"),
            ("2026-10-25", "10/25(日)"),
            ("2026-11-02", "11/2(月)"),
            ("2026-01-01", "1/1(木)"),
        ],
    )
    def test_formats_month_day_weekday(self, work_date, expected):
        assert format_date_short(work_date) == expected

    def test_invalid_date_is_shown_as_is(self):
        assert format_date_short("2026-13-01") == "2026-13-01"


class TestFormatBaseAvailability:
    def test_normal_weekday(self):
        assert format_base_availability(condition(TUESDAY)) == "通常"

    def test_normal_off_weekday(self):
        assert format_base_availability(condition(WEDNESDAY)) == "通常休み"


class TestFormatDayCondition:
    def test_no_change_is_shown_as_normal(self):
        """変更なしの日は「未入力」ではなく「通常」と表示する."""
        assert format_day_condition(condition(TUESDAY)) == NORMAL_LABEL

    def test_normal_off_weekday(self):
        assert format_day_condition(condition(WEDNESDAY)) == "通常休み"

    def test_absolute_off(self):
        assert format_day_condition(condition(TUESDAY, absolute_off=True)) == "絶休"

    def test_prefer_off(self):
        assert format_day_condition(condition(TUESDAY, prefer_off=True)) == "希休"

    def test_early_leave(self):
        assert format_day_condition(condition(TUESDAY, override_end_time="13:00")) == "13:00まで"

    def test_late_start(self):
        assert format_day_condition(condition(TUESDAY, override_start_time="10:00")) == "10:00から"

    def test_both_overrides(self):
        text = format_day_condition(
            condition(TUESDAY, override_start_time="10:00", override_end_time="13:00")
        )
        assert text == "10:00-13:00"

    def test_prefer_off_with_early_leave(self):
        text = format_day_condition(
            condition(TUESDAY, prefer_off=True, override_end_time="13:00")
        )
        assert text == "希休 13:00まで"

    def test_available_extra_on_normal_off_weekday(self):
        assert format_day_condition(condition(WEDNESDAY, available_extra=True)) == "勤務可"

    def test_available_extra_with_late_start(self):
        text = format_day_condition(
            condition(WEDNESDAY, available_extra=True, override_start_time="10:00")
        )
        assert text == "勤務可 10:00から"

    def test_standard_time_is_not_repeated(self):
        """通常どおりの時刻は表示しない（変更点だけを示す）."""
        assert "09:00" not in format_day_condition(condition(TUESDAY))
        assert "09:00" not in format_day_condition(condition(TUESDAY, prefer_off=True))

    def test_note_is_marked(self):
        assert format_day_condition(condition(TUESDAY, note="通院")) == NORMAL_LABEL + NOTE_MARK

    def test_note_is_marked_with_absolute_off(self):
        assert format_day_condition(condition(TUESDAY, absolute_off=True, note="通院")) == (
            "絶休" + NOTE_MARK
        )

    def test_time_unset_is_visible(self):
        staff = make_staff(standard_start_time=None, standard_end_time=None)
        assert format_day_condition(condition(TUESDAY, staff=staff)) == "時間未設定"

    def test_time_invalid_is_visible(self):
        text = format_day_condition(condition(TUESDAY, override_start_time="16:00"))
        assert "時間矛盾" in text


class TestFormatEffectiveTime:
    def test_includes_minutes(self):
        assert format_effective_time(condition(TUESDAY)) == "09:00-15:30（390分）"

    def test_early_leave_shortens_minutes(self):
        assert format_effective_time(condition(TUESDAY, override_end_time="13:00")) == (
            "09:00-13:00（240分）"
        )

    def test_absolute_off_has_no_working_time(self):
        assert format_effective_time(condition(TUESDAY, absolute_off=True)) == "勤務なし"

    def test_normal_off_weekday_has_no_working_time(self):
        assert format_effective_time(condition(WEDNESDAY)) == "勤務なし"

    def test_unset_standard_time_is_reported(self):
        staff = make_staff(standard_start_time=None, standard_end_time=None)
        assert format_effective_time(condition(TUESDAY, staff=staff)) == (
            "通常勤務時間が未設定です"
        )
