from datetime import date

import pytest

from src.month_utils import (
    get_month_dates,
    parse_year_month,
    round_half_up_workdays,
    weekday_index,
)


def test_parse_year_month():
    assert parse_year_month("2026-10") == (2026, 10)
    assert parse_year_month("2026-01") == (2026, 1)


@pytest.mark.parametrize(
    "value", ["2026-13", "2026-00", "2026-1", "202610", "2026/10", "", "2026-10-01", None, 202610]
)
def test_parse_year_month_invalid(value):
    with pytest.raises(ValueError):
        parse_year_month(value)


@pytest.mark.parametrize(
    "year_month, days",
    [
        ("2026-02", 28),  # 平年2月
        ("2028-02", 29),  # 閏年2月
        ("2026-04", 30),
        ("2026-10", 31),
        ("2100-02", 28),  # 100で割り切れる非閏年
        ("2000-02", 29),  # 400で割り切れる閏年
    ],
)
def test_get_month_dates_length(year_month, days):
    dates = get_month_dates(year_month)
    assert len(dates) == days
    assert dates[0] == f"{year_month}-01"
    assert dates[-1] == f"{year_month}-{days:02d}"


def test_get_month_dates_sorted_and_format():
    dates = get_month_dates("2026-10")
    assert dates == sorted(dates)
    assert dates[:3] == ["2026-10-01", "2026-10-02", "2026-10-03"]


def test_get_month_dates_invalid():
    with pytest.raises(ValueError):
        get_month_dates("2026-13")


def test_weekday_index():
    assert weekday_index("2026-09-21") == 0  # Monday
    assert weekday_index("2026-09-27") == 6  # Sunday
    assert weekday_index(date(2026, 9, 23)) == 2  # Wednesday


def test_weekday_index_invalid():
    with pytest.raises(ValueError):
        weekday_index("2026-02-30")


@pytest.mark.parametrize(
    "target, daily, expected",
    [
        (9600, 480, 20),  # 割り切れる
        (9840, 480, 21),  # 端数ちょうど半分 → 切り上げ
        (9839, 480, 20),  # 半分未満 → 切り捨て
        (9900, 480, 21),  # 半分超 → 切り上げ
        (0, 480, 0),
        (1800, 360, 5),
        (450, 300, 2),  # 1.5日 → 2
    ],
)
def test_round_half_up_workdays(target, daily, expected):
    assert round_half_up_workdays(target, daily) == expected


def test_round_half_up_workdays_odd_daily_minutes():
    # daily=5 → daily//2=2: 2.4日(12分)→2, 2.6日(13分)→3
    assert round_half_up_workdays(12, 5) == 2
    assert round_half_up_workdays(13, 5) == 3


@pytest.mark.parametrize("target, daily", [(480, 0), (480, -1), (-1, 480)])
def test_round_half_up_workdays_invalid(target, daily):
    with pytest.raises(ValueError):
        round_half_up_workdays(target, daily)
