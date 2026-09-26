"""テスト用の架空勤怠CSVを組み立てるヘルパー.

実データ（個人情報）は使わない。構造だけ実データ（freee人事労務の月間シフト出力）と同じにする。
"""

import csv
import io

from src.month_utils import get_month_dates

FIXED_HEADER = ["従業員番号", "freee人事労務での表示名", "部門"]

# (従業員番号, 氏名, 部門, 既定セル値)
DEFAULT_EMPLOYEES = [
    ("0015", "テスト清掃A", "清掃", "09:00-15:30"),
    ("0016", "テスト清掃B", "清掃", "深夜フロント（夜勤）"),
    ("0020", "テスト朝A", "朝", "05:30-11:30"),
]


def make_rows(year_month="2026-09", employees=None, *, cells=None, dates=None, extra_columns=()):
    """ヘッダー + 従業員行のリストを作る.

    cells: {(employee_code, 'YYYY-MM-DD'): 値} で個別セルを上書きする。
    dates: 日付列を明示する場合（不足・別月混在のテスト用）。
    extra_columns: 日付列の後ろに追加する列名（値は空）。
    """
    employees = DEFAULT_EMPLOYEES if employees is None else employees
    cells = cells or {}
    dates = get_month_dates(year_month) if dates is None else list(dates)
    rows = [FIXED_HEADER + dates + list(extra_columns)]
    for code, name, department, default in employees:
        row = [code, name, department]
        row += [cells.get((code, d), default) for d in dates]
        row += ["" for _ in extra_columns]
        rows.append(row)
    return rows


def to_csv_bytes(rows, encoding="utf-8-sig") -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    return buffer.getvalue().encode(encoding)


def make_csv(year_month="2026-09", employees=None, *, encoding="utf-8-sig", **kw) -> bytes:
    return to_csv_bytes(make_rows(year_month, employees, **kw), encoding)
