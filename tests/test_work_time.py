"""通常勤務時刻（HH:MM）の解析と標準勤務分の計算のテスト."""

import pytest

from src.work_time import (
    format_minutes,
    format_standard_work_time,
    is_valid_hhmm,
    normalize_hhmm,
    parse_hhmm,
    standard_work_minutes,
)


class TestParseHhmm:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("00:00", 0),
            ("09:00", 540),
            ("9:00", 540),       # 1桁時も受け付ける
            (" 09:30 ", 570),    # 前後空白は無視
            ("15:30", 930),
            ("23:59", 1439),
        ],
    )
    def test_valid(self, text, expected):
        assert parse_hhmm(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "24:00",   # 範囲外
            "30:00",   # 勤怠CSVの翌日跨ぎは通常勤務では扱わない
            "09:60",
            "0900",
            "9時",
            "09:0",
            "",
            "   ",
            None,
            540,
            True,
        ],
    )
    def test_invalid(self, text):
        assert parse_hhmm(text) is None
        assert is_valid_hhmm(text) is False


def test_normalize_hhmm():
    assert normalize_hhmm("9:00") == "09:00"
    assert normalize_hhmm("09:00") == "09:00"
    assert normalize_hhmm("30:00") is None


def test_format_minutes():
    assert format_minutes(0) == "00:00"
    assert format_minutes(540) == "09:00"
    assert format_minutes(1439) == "23:59"


class TestStandardWorkMinutes:
    def test_computed_from_start_and_end(self):
        assert standard_work_minutes("09:00", "15:30") == 390
        assert standard_work_minutes("09:00", "13:00") == 240

    @pytest.mark.parametrize(
        ("start", "end"),
        [
            ("09:00", "09:00"),   # 終了 > 開始 が必須
            ("15:00", "09:00"),
            ("09:00", None),      # 片方のみでは計算できない
            (None, "15:30"),
            (None, None),
            ("09:00", "30:00"),
        ],
    )
    def test_none_when_not_computable(self, start, end):
        assert standard_work_minutes(start, end) is None


class TestFormatStandardWorkTime:
    def test_includes_minutes(self):
        assert format_standard_work_time("09:00", "15:30") == "09:00-15:30（390分）"

    def test_normalizes_single_digit_hour(self):
        assert format_standard_work_time("9:00", "13:00") == "09:00-13:00（240分）"

    def test_blank_when_unset(self):
        assert format_standard_work_time(None, None) == ""
        assert format_standard_work_time("", "") == ""

    def test_incomplete_pair_is_visible(self):
        # 設定が不完全なことが画面で分かるよう入力値を残す
        assert format_standard_work_time("09:00", None) == "09:00-?"
        assert format_standard_work_time(None, "15:30") == "?-15:30"
