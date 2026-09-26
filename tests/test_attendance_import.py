"""勤怠CSVパーサ（DB非依存）のテスト."""

import pytest

from attendance_csv import FIXED_HEADER, make_csv, make_rows, to_csv_bytes
from src import attendance_import as ai
from src.attendance_import import (
    is_available_for_cleaning,
    parse_attendance_csv,
    parse_shift_cell,
    parse_time_range,
    source_basename,
)
from src.month_utils import get_month_dates


def _codes(issues):
    return [i.code for i in issues]


def _parse(data, name="shift.csv"):
    return parse_attendance_csv(data, name)


def _cell(result, code, work_date):
    return next(s for s in result.shifts if (s.employee_code, s.work_date) == (code, work_date))


# ---------------------------------------------------------------------------
# 文字コード
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-8", "cp932"])
def test_decode_supported_encodings(encoding):
    result = _parse(make_csv(encoding=encoding))
    assert result.errors == []
    assert result.year_month == "2026-09"
    assert _cell(result, "0015", "2026-09-01").employee_name == "テスト清掃A"
    assert _cell(result, "0016", "2026-09-01").raw_shift == "深夜フロント（夜勤）"


def test_decode_failure_is_fatal():
    result = _parse(b"\xff\xfe\x00\x81\xff\xff")
    assert _codes(result.errors) == [ai.DECODE_FAILED]
    assert result.shifts == []
    assert result.year_month is None


def test_empty_file_is_fatal():
    assert _codes(_parse(b"").errors) == [ai.EMPTY_FILE]


def test_header_only_is_fatal():
    rows = make_rows(employees=[])
    assert _codes(_parse(to_csv_bytes(rows)).errors) == [ai.NO_EMPLOYEE_ROWS]


# ---------------------------------------------------------------------------
# 従業員番号
# ---------------------------------------------------------------------------


def test_employee_code_leading_zero_preserved_and_distinct():
    employees = [
        ("0015", "テストA", "清掃", "09:00-15:30"),
        ("15", "テストB", "清掃", "09:00-15:30"),
        ("00100", "テストC", "清掃", "09:00-15:30"),
    ]
    result = _parse(make_csv(employees=employees))
    assert result.errors == []
    assert sorted({s.employee_code for s in result.shifts}) == ["00100", "0015", "15"]


def test_employee_code_whitespace_stripped():
    employees = [(" 0015 ", "テストA", "清掃", "09:00-15:30")]
    result = _parse(make_csv(employees=employees))
    assert {s.employee_code for s in result.shifts} == {"0015"}


def test_duplicate_employee_code_is_fatal():
    employees = [
        ("0015", "テストA", "清掃", "09:00-15:30"),
        ("0015", "テストB", "清掃", "09:00-15:30"),
    ]
    result = _parse(make_csv(employees=employees))
    assert _codes(result.errors) == [ai.EMPLOYEE_CODE_DUPLICATE]
    assert "3行目" in result.errors[0].message and "2行目" in result.errors[0].message


@pytest.mark.parametrize("code", ["", "   "])
def test_blank_employee_code_is_fatal(code):
    employees = [(code, "テストA", "清掃", "09:00-15:30")]
    result = _parse(make_csv(employees=employees))
    assert _codes(result.errors) == [ai.EMPLOYEE_CODE_BLANK]
    assert result.errors[0].row_number == 2


def test_completely_blank_row_is_ignored():
    rows = make_rows()
    rows.insert(2, [""] * len(rows[0]))
    rows.append([" "] * len(rows[0]))
    result = _parse(to_csv_bytes(rows))
    assert result.errors == []
    assert len({s.employee_code for s in result.shifts}) == 3


def test_row_column_count_mismatch_is_fatal():
    rows = make_rows()
    rows[1] = rows[1][:-1]
    result = _parse(to_csv_bytes(rows))
    assert _codes(result.errors) == [ai.ROW_COLUMN_COUNT_MISMATCH]


# ---------------------------------------------------------------------------
# 日付列・対象年月
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "year_month,days",
    [("2026-09", 30), ("2026-10", 31), ("2026-02", 28), ("2028-02", 29)],
)
def test_full_month_is_parsed(year_month, days):
    result = _parse(make_csv(year_month))
    assert result.errors == []
    assert result.year_month == year_month
    # BLANKを含む全従業員 × 全日付
    assert len(result.shifts) == 3 * days
    assert {s.work_date for s in result.shifts} == set(get_month_dates(year_month))


def test_missing_date_is_fatal():
    dates = [d for d in get_month_dates("2026-09") if d != "2026-09-15"]
    result = _parse(make_csv(dates=dates))
    assert _codes(result.errors) == [ai.MISSING_DATES]
    assert "2026-09-15" in result.errors[0].message
    assert result.shifts == []


def test_leap_day_missing_is_fatal():
    dates = get_month_dates("2028-02")[:-1]  # 2/29 なし
    assert _codes(_parse(make_csv(dates=dates)).errors) == [ai.MISSING_DATES]


def test_other_month_date_is_fatal():
    dates = get_month_dates("2026-09") + ["2026-10-01"]
    result = _parse(make_csv(dates=dates))
    assert _codes(result.errors) == [ai.MULTIPLE_MONTHS]
    assert result.year_month is None


def test_duplicate_date_column_is_fatal():
    dates = get_month_dates("2026-09") + ["2026-09-30"]
    assert ai.DUPLICATE_COLUMN in _codes(_parse(make_csv(dates=dates)).errors)


@pytest.mark.parametrize("bad", ["2026-02-30", "2026/09/01", "2026-9-1", "2026年9月1日"])
def test_invalid_date_column_is_fatal(bad):
    dates = get_month_dates("2026-09") + [bad]
    assert ai.INVALID_DATE_COLUMN in _codes(_parse(make_csv(dates=dates)).errors)


def test_no_date_columns_is_fatal():
    assert ai.NO_DATE_COLUMNS in _codes(_parse(make_csv(dates=[])).errors)


# ---------------------------------------------------------------------------
# 固定列・未知列
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("column", FIXED_HEADER)
def test_missing_fixed_column_is_fatal(column):
    rows = make_rows()
    index = rows[0].index(column)
    rows = [row[:index] + row[index + 1:] for row in rows]
    result = _parse(to_csv_bytes(rows))
    assert _codes(result.errors) == [ai.MISSING_FIXED_COLUMN]
    assert column in result.errors[0].message


@pytest.mark.parametrize("extra", ["備考", "所定労働時間", ""])
def test_unknown_column_is_fatal(extra):
    result = _parse(make_csv(extra_columns=[extra]))
    assert _codes(result.errors) == [ai.UNKNOWN_COLUMN]
    assert result.shifts == []


def test_header_whitespace_is_stripped():
    rows = make_rows()
    rows[0] = [f" {h} " for h in rows[0]]
    assert _parse(to_csv_bytes(rows)).errors == []


# ---------------------------------------------------------------------------
# 氏名・部門の空欄
# ---------------------------------------------------------------------------


def test_blank_name_and_department_are_warnings():
    employees = [("0015", "", "", "09:00-15:30"), ("0016", " ", "清掃", "")]
    result = _parse(make_csv(employees=employees))
    assert result.errors == []
    assert sorted(_codes(result.warnings)) == sorted(
        [ai.EMPLOYEE_NAME_BLANK, ai.DEPARTMENT_BLANK, ai.EMPLOYEE_NAME_BLANK]
    )
    cell = _cell(result, "0015", "2026-09-01")
    assert cell.employee_name is None and cell.department is None
    # 部門空欄は清掃勤務として数えない
    assert cell.available_for_cleaning is False


# ---------------------------------------------------------------------------
# TIME_RANGE
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("09:00-15:30", (540, 930)),
        ("06:00-18:00", (360, 1080)),
        ("23:00-30:00", (1380, 1800)),  # 24時超え（翌6:00）
        ("00:00-24:00", (0, 1440)),  # 勤務時間ちょうど24時間は有効（上限境界）
        ("23:59-47:59", (1439, 2879)),  # 時の上限47
        ("09:00-09:01", (540, 541)),  # 最短1分
        (" 09:00-15:30 ", (540, 930)),  # 前後空白は無視
    ],
)
def test_parse_time_range_valid(text, expected):
    assert parse_time_range(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "30:00-23:00",  # end < start
        "09:00-09:00",  # end == start
        "25:99-30:00",  # 分 > 59
        "09:00-40:00",  # 31時間（24時間超）
        "00:00-24:01",  # 1441分（24時間超の境界）
        "48:00-49:00",  # 時 > 47
        "9:00-15:30",  # 時は2桁必須
        "09:00 - 15:30",  # 区切り前後の空白は不可
        "09:00〜15:30",
        "０９：００－１５：３０",  # 全角
        "09:00-15:30-16:00",
        "abc",
    ],
)
def test_parse_time_range_invalid(text):
    assert parse_time_range(text) is None


# ---------------------------------------------------------------------------
# セル分類
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["", "   ", "\u3000", None])
def test_blank_cells(raw):
    cell = parse_shift_cell(raw)
    assert cell.shift_type == "BLANK"
    assert cell.raw_shift == ""
    assert cell.start_minutes is None and cell.end_minutes is None


def test_time_range_cell():
    cell = parse_shift_cell("23:00-30:00")
    assert (cell.shift_type, cell.raw_shift, cell.start_minutes, cell.end_minutes) == (
        "TIME_RANGE", "23:00-30:00", 1380, 1800
    )


@pytest.mark.parametrize("raw", ["深夜フロント（夜勤）", " 深夜フロント（夜勤） "])
def test_other_duty_cell_keeps_raw(raw):
    cell = parse_shift_cell(raw)
    assert cell.shift_type == "OTHER_DUTY"
    assert cell.raw_shift == raw


@pytest.mark.parametrize("raw", ["特別勤務A", "30:00-23:00", "NA", "nan", "休"])
def test_unknown_cell_keeps_raw(raw):
    cell = parse_shift_cell(raw)
    assert cell.shift_type == "UNKNOWN"
    assert cell.raw_shift == raw


def test_blank_csv_cell_is_not_stored_as_nan():
    result = _parse(make_csv(cells={("0015", "2026-09-02"): ""}))
    cell = _cell(result, "0015", "2026-09-02")
    assert cell.shift_type == "BLANK"
    assert cell.raw_shift == ""


def test_unknown_cell_warning():
    result = _parse(make_csv(cells={("0015", "2026-09-15"): "特別勤務A"}))
    assert result.errors == []
    (warning,) = [w for w in result.warnings if w.code == ai.UNKNOWN_SHIFT]
    assert warning.employee_code == "0015"
    assert warning.work_date == "2026-09-15"
    assert warning.message == '未認識勤務区分: "特別勤務A"'
    assert _cell(result, "0015", "2026-09-15").raw_shift == "特別勤務A"


# ---------------------------------------------------------------------------
# 清掃勤務判定
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "department,shift_type,expected",
    [
        ("清掃", "TIME_RANGE", True),
        (" 清掃 ", "TIME_RANGE", True),
        ("清掃", "OTHER_DUTY", False),
        ("朝", "TIME_RANGE", False),
        ("清掃", "BLANK", False),
        ("清掃", "UNKNOWN", False),
        (None, "TIME_RANGE", False),
    ],
)
def test_is_available_for_cleaning(department, shift_type, expected):
    assert is_available_for_cleaning(department, shift_type) is expected


def test_available_for_cleaning_in_parsed_result():
    cells = {("0015", "2026-09-02"): "", ("0015", "2026-09-03"): "特別勤務A"}
    result = _parse(make_csv(cells=cells))
    assert _cell(result, "0015", "2026-09-01").available_for_cleaning is True  # 清掃+時間帯
    assert _cell(result, "0015", "2026-09-02").available_for_cleaning is False  # 清掃+BLANK
    assert _cell(result, "0015", "2026-09-03").available_for_cleaning is False  # 清掃+UNKNOWN
    assert _cell(result, "0016", "2026-09-01").available_for_cleaning is False  # 清掃+別業務
    assert _cell(result, "0020", "2026-09-01").available_for_cleaning is False  # 朝+時間帯
    assert all(s.staff_id is None for s in result.shifts)  # パーサは照合しない


# ---------------------------------------------------------------------------
# ファイル名
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["shift.csv", "C:\\Users\\someone\\Downloads\\shift.csv", "/tmp/dir/shift.csv"],
)
def test_source_basename(name):
    assert source_basename(name) == "shift.csv"
    assert _parse(make_csv(), name).source_filename == "shift.csv"
