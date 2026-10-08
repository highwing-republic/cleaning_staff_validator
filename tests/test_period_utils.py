"""対象期間（開始日＋日数）のユーティリティのテスト."""

from datetime import date

import pytest

from src.period_utils import (
    PERIOD_DAY_OPTIONS,
    PERIOD_MAX_DAYS,
    default_period_start,
    format_period,
    is_valid_date,
    parse_date,
    period_dates,
    period_end_date,
)


class TestParseDate:
    @pytest.mark.parametrize(
        "value", ["2026-10-20", "2026-01-01", "2026-12-31", "2028-02-29"]
    )
    def test_valid(self, value):
        assert parse_date(value) == date.fromisoformat(value)
        assert is_valid_date(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "2026-13-01",
            "2026-02-30",
            "2026-02-29",  # 閏年ではない
            "2026-2-3",
            "20261020",
            "2026/10/20",
            "",
            "   ",
            None,
            20261020,
        ],
    )
    def test_invalid(self, value):
        assert parse_date(value) is None
        assert is_valid_date(value) is False

    def test_date_object_passes_through(self):
        assert parse_date(date(2026, 10, 20)) == date(2026, 10, 20)


class TestPeriodDates:
    def test_returns_requested_number_of_days(self):
        dates = period_dates("2026-10-20", 14)
        assert len(dates) == 14
        assert dates[0] == "2026-10-20"
        assert dates[-1] == "2026-11-02"

    def test_crosses_month_boundary(self):
        """月をまたぐ期間を扱えること（現場は月単位で組んでいない）."""
        dates = period_dates("2026-10-28", 10)
        assert dates[0] == "2026-10-28"
        assert "2026-10-31" in dates
        assert "2026-11-01" in dates
        assert dates[-1] == "2026-11-06"

    def test_crosses_year_boundary(self):
        dates = period_dates("2026-12-28", 10)
        assert dates[0] == "2026-12-28"
        assert dates[-1] == "2027-01-06"

    def test_is_ascending_and_contiguous(self):
        dates = period_dates("2026-10-20", 14)
        assert dates == sorted(dates)
        assert len(set(dates)) == 14

    @pytest.mark.parametrize("days", [1, 10, 11, 12, 13, 14])
    def test_valid_day_counts(self, days):
        assert len(period_dates("2026-10-20", days)) == days

    @pytest.mark.parametrize("days", [0, -1, 15, 30, 31])
    def test_rejects_out_of_range_days(self, days):
        """最大14日まで（それ以上は画面が重く、紙からの転記もしづらい）."""
        with pytest.raises(ValueError):
            period_dates("2026-10-20", days)

    @pytest.mark.parametrize("days", [True, 14.0, "14", None])
    def test_rejects_non_int_days(self, days):
        with pytest.raises(ValueError):
            period_dates("2026-10-20", days)

    @pytest.mark.parametrize("start", ["2026-13-01", "2026-02-30", "x", "", None])
    def test_rejects_invalid_start(self, start):
        with pytest.raises(ValueError):
            period_dates(start, 14)


def test_period_max_days_is_fourteen():
    assert PERIOD_MAX_DAYS == 14
    assert max(PERIOD_DAY_OPTIONS) == PERIOD_MAX_DAYS


def test_period_end_date():
    assert period_end_date("2026-10-20", 14) == "2026-11-02"


def test_default_period_start_is_tomorrow():
    """今日への依存はこの関数だけに閉じ込める（テストを安定させるため）."""
    assert default_period_start(date(2026, 10, 8)) == "2026-10-09"
    assert default_period_start(date(2026, 12, 31)) == "2027-01-01"


def test_default_period_start_without_argument_is_valid_date():
    assert is_valid_date(default_period_start())


def test_format_period():
    assert format_period("2026-10-20", 14) == "2026-10-20 ～ 2026-11-02（14日間）"
