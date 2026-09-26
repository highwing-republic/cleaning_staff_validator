"""勤怠CSV（月間シフト）の解析.

DB/Streamlitに依存しない純粋ロジック。責務は
decode → 列検証 → 対象年月の判定 → 勤務セル分類 → 解析結果の組み立て まで。
staff masterとの照合・保存は services 側で行う。

pandasは使わず標準csvモジュールで全セルを文字列のまま読む。
（型推論で従業員番号の先頭0が落ちること、空欄がNaN/"nan"になること、
重複ヘッダーが "列名.1" に自動改名されて検出できなくなることを避けるため）
"""

import csv
import io
import re
from dataclasses import dataclass
from datetime import date
from pathlib import PureWindowsPath

from src.constants import (
    ATTENDANCE_COLUMN_DEPARTMENT,
    ATTENDANCE_COLUMN_EMPLOYEE_CODE,
    ATTENDANCE_COLUMN_EMPLOYEE_NAME,
    ATTENDANCE_FIXED_COLUMNS,
    CLEANING_DEPARTMENT,
    CSV_ENCODINGS,
    OTHER_DUTY_LABELS,
    SHIFT_HOUR_MAX,
    SHIFT_MAX_DURATION_MINUTES,
    SHIFT_TYPE_BLANK,
    SHIFT_TYPE_OTHER_DUTY,
    SHIFT_TYPE_TIME_RANGE,
    SHIFT_TYPE_UNKNOWN,
)
from src.models import AttendanceParseResult, AttendanceShiftInput, ImportIssue
from src.month_utils import get_month_dates

# ---------------------------------------------------------------------------
# fatal error コード（1件でもあれば取込不可）
# ---------------------------------------------------------------------------

DECODE_FAILED = "ATTENDANCE_DECODE_FAILED"
CSV_MALFORMED = "ATTENDANCE_CSV_MALFORMED"
EMPTY_FILE = "ATTENDANCE_EMPTY_FILE"
MISSING_FIXED_COLUMN = "ATTENDANCE_MISSING_FIXED_COLUMN"
DUPLICATE_COLUMN = "ATTENDANCE_DUPLICATE_COLUMN"
UNKNOWN_COLUMN = "ATTENDANCE_UNKNOWN_COLUMN"
INVALID_DATE_COLUMN = "ATTENDANCE_INVALID_DATE_COLUMN"
NO_DATE_COLUMNS = "ATTENDANCE_NO_DATE_COLUMNS"
MULTIPLE_MONTHS = "ATTENDANCE_MULTIPLE_MONTHS"
MISSING_DATES = "ATTENDANCE_MISSING_DATES"
ROW_COLUMN_COUNT_MISMATCH = "ATTENDANCE_ROW_COLUMN_COUNT_MISMATCH"
EMPLOYEE_CODE_BLANK = "ATTENDANCE_EMPLOYEE_CODE_BLANK"
EMPLOYEE_CODE_DUPLICATE = "ATTENDANCE_EMPLOYEE_CODE_DUPLICATE"
NO_EMPLOYEE_ROWS = "ATTENDANCE_NO_EMPLOYEE_ROWS"

# warning コード（取込は可能）
EMPLOYEE_NAME_BLANK = "ATTENDANCE_EMPLOYEE_NAME_BLANK"
DEPARTMENT_BLANK = "ATTENDANCE_DEPARTMENT_BLANK"
UNKNOWN_SHIFT = "ATTENDANCE_UNKNOWN_SHIFT"

_DATE_COLUMN_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 日付らしいが YYYY-MM-DD ではない列名（形式不正として明示的に報告する）
_DATE_LIKE_PATTERN = re.compile(r"^\d{4}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}\s*日?$")
_TIME_RANGE_PATTERN = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$")


class AttendanceDecodeError(Exception):
    """utf-8-sig / cp932 のいずれでも読めない場合."""


@dataclass(frozen=True)
class ParsedShiftCell:
    shift_type: str
    raw_shift: str
    start_minutes: int | None = None
    end_minutes: int | None = None


# ---------------------------------------------------------------------------
# decode / read
# ---------------------------------------------------------------------------


def decode_csv_bytes(data: bytes) -> str:
    """utf-8-sig → cp932 の順でデコードする. 両方失敗したらAttendanceDecodeError."""
    for encoding in CSV_ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise AttendanceDecodeError(
        "文字コードを判別できません。utf-8またはShift-JIS(cp932)で保存してください。"
    )


def read_attendance_csv(data: bytes) -> list[list[str]]:
    """CSVを全セル文字列のまま行リストとして読む（型変換しない）."""
    text = decode_csv_bytes(data)
    return list(csv.reader(io.StringIO(text, newline="")))


def source_basename(filename: str) -> str:
    """アップロード名からフォルダ部分を除く（/ と \\ の両方に対応）."""
    return PureWindowsPath(filename).name


# ---------------------------------------------------------------------------
# columns / year_month
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttendanceColumns:
    fixed_index: dict[str, int]
    # (列index, 'YYYY-MM-DD') を列順で保持
    date_columns: list[tuple[int, str]]
    year_month: str


def _parse_date_column(name: str) -> str | None:
    if not _DATE_COLUMN_PATTERN.match(name):
        return None
    try:
        return date.fromisoformat(name).isoformat()
    except ValueError:
        return None


def validate_attendance_columns(
    header: list[str],
) -> tuple[AttendanceColumns | None, list[ImportIssue]]:
    """ヘッダーを検証し、固定列・日付列・対象年月を返す.

    1ファイル = 1か月分の全日付が必須（不足・重複・別月混在は fatal）。
    固定列・日付列以外の列は fatal（黙って無視しない）。
    """
    errors: list[ImportIssue] = []
    names = [h.strip() for h in header]

    seen: set[str] = set()
    for name in names:
        if name in seen:
            errors.append(
                ImportIssue(DUPLICATE_COLUMN, f"列「{name}」が重複しています。", row_number=1)
            )
        seen.add(name)

    fixed_index: dict[str, int] = {}
    for column in ATTENDANCE_FIXED_COLUMNS:
        if column in names:
            fixed_index[column] = names.index(column)
        else:
            errors.append(
                ImportIssue(MISSING_FIXED_COLUMN, f"必須列「{column}」がありません。", row_number=1)
            )

    date_columns: list[tuple[int, str]] = []
    for index, name in enumerate(names):
        if name in ATTENDANCE_FIXED_COLUMNS:
            continue
        work_date = _parse_date_column(name)
        if work_date is not None:
            date_columns.append((index, work_date))
        elif _DATE_COLUMN_PATTERN.match(name) or _DATE_LIKE_PATTERN.match(name):
            errors.append(
                ImportIssue(
                    INVALID_DATE_COLUMN,
                    f"日付列「{name}」の形式が不正です（YYYY-MM-DD の実在する日付が必要です）。",
                    row_number=1,
                )
            )
        else:
            label = name if name else f"{index + 1}列目（列名なし）"
            errors.append(
                ImportIssue(
                    UNKNOWN_COLUMN,
                    f"未対応の列「{label}」があります。対応列は固定列と日付列のみです。",
                    row_number=1,
                )
            )

    year_month = None
    if not date_columns:
        errors.append(ImportIssue(NO_DATE_COLUMNS, "日付列（YYYY-MM-DD）がありません。", row_number=1))
    else:
        months = sorted({d[:7] for _, d in date_columns})
        if len(months) > 1:
            errors.append(
                ImportIssue(
                    MULTIPLE_MONTHS,
                    f"複数の月の日付列が含まれています（{', '.join(months)}）。1ファイル1か月分にしてください。",
                    row_number=1,
                )
            )
        else:
            year_month = months[0]
            present = {d for _, d in date_columns}
            missing = [d for d in get_month_dates(year_month) if d not in present]
            if missing:
                errors.append(
                    ImportIssue(
                        MISSING_DATES,
                        f"{year_month} の日付列が不足しています: {', '.join(missing)}"
                        "（部分月のCSVは取り込めません）。",
                        row_number=1,
                    )
                )

    if errors:
        return None, errors
    return AttendanceColumns(fixed_index, date_columns, year_month), []


# ---------------------------------------------------------------------------
# cells
# ---------------------------------------------------------------------------


def _to_minutes(hour: str, minute: str) -> int | None:
    h, m = int(hour), int(minute)
    if not 0 <= h <= SHIFT_HOUR_MAX or not 0 <= m <= 59:
        return None
    return h * 60 + m


def parse_time_range(text: str) -> tuple[int, int] | None:
    """'HH:MM-HH:MM' を (start_minutes, end_minutes) にする. 条件外はNone.

    - 時は 00〜47（24時超え = 翌日。例 30:00 = 1800分）、分は 00〜59、いずれも2桁
    - end > start かつ 勤務時間 (end - start) は 1440分(24時間) 以内
    """
    match = _TIME_RANGE_PATTERN.match(text.strip())
    if match is None:
        return None
    start = _to_minutes(match.group(1), match.group(2))
    end = _to_minutes(match.group(3), match.group(4))
    if start is None or end is None:
        return None
    if end <= start or end - start > SHIFT_MAX_DURATION_MINUTES:
        return None
    return start, end


def parse_shift_cell(raw: str | None) -> ParsedShiftCell:
    """勤務セルを TIME_RANGE / OTHER_DUTY / BLANK / UNKNOWN に分類する.

    BLANK（空・空白のみ）は raw_shift="" とする。それ以外は元の値をそのまま保持する。
    """
    if raw is None or not raw.strip():
        return ParsedShiftCell(SHIFT_TYPE_BLANK, "")

    time_range = parse_time_range(raw)
    if time_range is not None:
        return ParsedShiftCell(SHIFT_TYPE_TIME_RANGE, raw, time_range[0], time_range[1])

    if raw.strip() in OTHER_DUTY_LABELS:
        return ParsedShiftCell(SHIFT_TYPE_OTHER_DUTY, raw)

    return ParsedShiftCell(SHIFT_TYPE_UNKNOWN, raw)


def is_available_for_cleaning(department: str | None, shift_type: str) -> bool:
    """CSVの部門が清掃 かつ TIME_RANGE のときだけ清掃勤務とする."""
    return (
        shift_type == SHIFT_TYPE_TIME_RANGE
        and department is not None
        and department.strip() == CLEANING_DEPARTMENT
    )


# ---------------------------------------------------------------------------
# build parsed result
# ---------------------------------------------------------------------------


def _optional_text(value: str) -> str | None:
    text = value.strip()
    return text or None


def parse_attendance_csv(data: bytes, source_filename: str) -> AttendanceParseResult:
    """勤怠CSVを解析する. fatal error は例外ではなく errors として返す."""
    filename = source_basename(source_filename)

    try:
        rows = read_attendance_csv(data)
    except AttendanceDecodeError as exc:
        return AttendanceParseResult(filename, errors=[ImportIssue(DECODE_FAILED, str(exc))])
    except csv.Error as exc:
        return AttendanceParseResult(
            filename, errors=[ImportIssue(CSV_MALFORMED, f"CSVを読み取れません: {exc}")]
        )

    if not rows or not any(cell.strip() for cell in rows[0]):
        return AttendanceParseResult(
            filename, errors=[ImportIssue(EMPTY_FILE, "CSVにヘッダー行がありません。")]
        )

    header = rows[0]
    columns, errors = validate_attendance_columns(header)
    if columns is None:
        return AttendanceParseResult(filename, errors=errors)

    code_index = columns.fixed_index[ATTENDANCE_COLUMN_EMPLOYEE_CODE]
    name_index = columns.fixed_index[ATTENDANCE_COLUMN_EMPLOYEE_NAME]
    department_index = columns.fixed_index[ATTENDANCE_COLUMN_DEPARTMENT]

    warnings: list[ImportIssue] = []
    shifts: list[AttendanceShiftInput] = []
    first_row_by_code: dict[str, int] = {}

    for row_number, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue  # 完全な空行は無視する
        if len(row) != len(header):
            errors.append(
                ImportIssue(
                    ROW_COLUMN_COUNT_MISMATCH,
                    f"{row_number}行目: 列数({len(row)})がヘッダー({len(header)})と一致しません。",
                    row_number=row_number,
                )
            )
            continue

        employee_code = row[code_index].strip()
        employee_name = _optional_text(row[name_index])
        department = _optional_text(row[department_index])

        if not employee_code:
            errors.append(
                ImportIssue(
                    EMPLOYEE_CODE_BLANK,
                    f"{row_number}行目: 従業員番号が空欄です。",
                    employee_name=employee_name,
                    row_number=row_number,
                )
            )
            continue
        if employee_code in first_row_by_code:
            errors.append(
                ImportIssue(
                    EMPLOYEE_CODE_DUPLICATE,
                    f"{row_number}行目: 従業員番号「{employee_code}」が"
                    f"{first_row_by_code[employee_code]}行目と重複しています。",
                    employee_code=employee_code,
                    employee_name=employee_name,
                    row_number=row_number,
                )
            )
            continue
        first_row_by_code[employee_code] = row_number

        if employee_name is None:
            warnings.append(
                ImportIssue(
                    EMPLOYEE_NAME_BLANK,
                    f"{row_number}行目: 氏名が空欄です。",
                    employee_code=employee_code,
                    row_number=row_number,
                )
            )
        if department is None:
            warnings.append(
                ImportIssue(
                    DEPARTMENT_BLANK,
                    f"{row_number}行目: 部門が空欄です（清掃勤務として数えません）。",
                    employee_code=employee_code,
                    employee_name=employee_name,
                    row_number=row_number,
                )
            )

        for index, work_date in columns.date_columns:
            cell = parse_shift_cell(row[index])
            if cell.shift_type == SHIFT_TYPE_UNKNOWN:
                warnings.append(
                    ImportIssue(
                        UNKNOWN_SHIFT,
                        f"未認識勤務区分: \"{cell.raw_shift}\"",
                        employee_code=employee_code,
                        employee_name=employee_name,
                        work_date=work_date,
                        row_number=row_number,
                    )
                )
            shifts.append(
                AttendanceShiftInput(
                    employee_code=employee_code,
                    work_date=work_date,
                    raw_shift=cell.raw_shift,
                    shift_type=cell.shift_type,
                    available_for_cleaning=is_available_for_cleaning(
                        department, cell.shift_type
                    ),
                    employee_name=employee_name,
                    department=department,
                    start_minutes=cell.start_minutes,
                    end_minutes=cell.end_minutes,
                )
            )

    if not first_row_by_code and not errors:
        errors.append(ImportIssue(NO_EMPLOYEE_ROWS, "従業員の行がありません。"))

    return AttendanceParseResult(
        source_filename=filename,
        year_month=columns.year_month,
        shifts=shifts,
        errors=errors,
        warnings=warnings,
    )
