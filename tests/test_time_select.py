"""時刻プルダウンの選択肢と、時刻の分解・結合（Streamlit非依存の部分）."""

import pytest

from src.time_select import (
    HOUR_CHOICES,
    MINUTE_CHOICES,
    format_hour,
    format_minute,
    hour_options,
    join_time,
    minute_options,
    split_time,
)


def test_hours_are_in_one_hour_steps():
    assert list(HOUR_CHOICES) == list(range(5, 24))


def test_minutes_are_zero_or_thirty():
    assert MINUTE_CHOICES == ("00", "30")


@pytest.mark.parametrize(
    ("value", "expected"),
    [("09:30", (9, "30")), ("9:00", (9, "00")), ("15:00", (15, "00")),
     (None, (None, "00")), ("", (None, "00")), ("25:00", (None, "00")), ("abc", (None, "00"))],
)
def test_split_time(value, expected):
    assert split_time(value) == expected


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [(9, "00", "09:00"), (15, "30", "15:30"), (None, "30", None)],
)
def test_join_time(hour, minute, expected):
    assert join_time(hour, minute) == expected


def test_hour_options_start_with_unset():
    options = hour_options(None)
    assert options[0] is None
    assert options[1:] == list(HOUR_CHOICES)


def test_hour_options_without_unset():
    assert hour_options(None, allow_unset=False) == list(HOUR_CHOICES)


def test_hour_options_keep_a_saved_hour_outside_the_range():
    options = hour_options("04:30")
    assert options[0] is None
    assert options[1] == 4
    assert options[1:] == sorted(options[1:])


def test_minute_options_keep_a_saved_odd_minute():
    assert minute_options("09:15") == ["00", "15", "30"]
    assert minute_options("09:30") == ["00", "30"]


def test_labels():
    assert format_hour(None) == "未設定"
    assert format_hour(9) == "9時"
    assert format_minute("30") == "30分"
