"""勤務表のExcel出力（印刷・配布用）.

Phase 4の validation_excel.py（勤怠CSV検証用）とは別物で、こちらは
現場へ配布する勤務表そのものを印刷できる形にする。

3シート:
    勤務表     … スタッフ × 日付。A4横・横1ページに収める設定を入れる
    日別確認   … 1日1行の必要人数・配置人数・不足
    問題一覧   … Issue 1件1行
    出力情報   … 出力日時・対象期間など（個人PCのパス等は出さない）

当日の変更は印刷した紙へ手書きする運用なので、行の高さと備考欄を確保する。
Solverの内部値（target_scaled 等）は出さない。
DB/Streamlitに依存しない。
"""

from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from src.export_excel import (
    TITLE_FONT,
    new_workbook,
    set_cell,
    weekday_fill,
    workbook_to_bytes,
    write_header_row,
)
from src.models import ScheduleExport
from src.period_utils import format_date_short
from src.schedule_output_display import (
    DRAFT_NOTICE,
    SEVERITY_LABELS,
    format_day_state,
    format_required,
    format_reserved_rooms,
    format_role,
    format_schedule_status,
    format_scheduled,
    format_skill,
    format_staff_count,
    severity_sort_key,
)

SHEET_SCHEDULE = "勤務表"
SHEET_DAILY = "日別確認"
SHEET_ISSUES = "問題一覧"
SHEET_INFO = "出力情報"

APP_NAME = "清掃スタッフ勤務表"
REMARKS_HEADER = "当日変更・備考"

# 印刷した紙へ手書きできるだけの高さ・幅を確保する
_ROW_HEIGHT = 24
_STAFF_COLUMN_WIDTH = 16
_ROLE_COLUMN_WIDTH = 10
_DATE_COLUMN_WIDTH = 12
_REMARKS_COLUMN_WIDTH = 24

_WRAP_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)


def schedule_excel_filename(work_dates: list[str]) -> str:
    if not work_dates:
        return "cleaning_schedule.xlsx"
    return f"cleaning_schedule_{work_dates[0]}_{work_dates[-1]}.xlsx"


def _set_widths(ws: Worksheet, widths: list[int]) -> None:
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(index)].width = width


def _setup_print(ws: Worksheet, freeze: str) -> None:
    """A4横・横1ページに収める（14日分でも1枚に入るようにする）."""
    ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_options.horizontalCentered = True
    ws.page_margins.left = ws.page_margins.right = 0.4
    ws.page_margins.top = ws.page_margins.bottom = 0.5
    ws.freeze_panes = freeze


# ---------------------------------------------------------------------------
# Sheet 1: 勤務表
# ---------------------------------------------------------------------------


def _build_schedule_sheet(wb: Workbook, export: ScheduleExport) -> None:
    ws = wb.create_sheet(SHEET_SCHEDULE)
    dates = export.work_dates
    validation = export.validation

    ws.cell(row=1, column=1, value=f"{APP_NAME}").font = TITLE_FONT
    period = f"{dates[0]} 〜 {dates[-1]}" if dates else "（対象期間なし）"
    ws.cell(row=2, column=1, value=f"対象期間: {period}")
    if validation.has_draft:
        # 確定前の勤務表を配ってしまわないよう、紙の上で分かるようにする
        ws.cell(row=3, column=1, value=DRAFT_NOTICE).font = Font(bold=True)

    header_row = 5
    headers = ["スタッフ", "役割"]
    headers += [format_date_short(d) for d in dates]
    headers += ["出勤日数", REMARKS_HEADER]
    write_header_row(ws, header_row, headers)

    # 日付ごとの状態（確定／下書き／未作成）を列見出しの下に文字で書く
    status_row = header_row + 1
    set_cell(ws, status_row, 1, "状態", bold=True, align_center=True)
    set_cell(ws, status_row, 2, None, align_center=True)
    for index, work_date in enumerate(dates):
        day = validation.day(work_date)
        set_cell(
            ws,
            status_row,
            3 + index,
            format_schedule_status(day) if day else "",
            bold=True,
            fill=weekday_fill(work_date),
            align_center=True,
        )
    set_cell(ws, status_row, 3 + len(dates), None, align_center=True)
    set_cell(ws, status_row, 4 + len(dates), None, align_center=True)

    for offset, row_data in enumerate(export.staff_rows):
        row = status_row + 1 + offset
        set_cell(ws, row, 1, row_data.staff.staff_name)
        set_cell(ws, row, 2, row_data.role_name, align_center=True)
        for index, work_date in enumerate(dates):
            set_cell(
                ws,
                row,
                3 + index,
                row_data.cells.get(work_date, ""),
                fill=weekday_fill(work_date),
                align_center=True,
            )
        set_cell(ws, row, 3 + len(dates), row_data.working_days, align_center=True)
        set_cell(ws, row, 4 + len(dates), None)  # 手書き用の空欄（罫線だけ引く）
        ws.row_dimensions[row].height = _ROW_HEIGHT

    ws.row_dimensions[header_row].height = _ROW_HEIGHT
    for cell in ws[header_row]:
        cell.alignment = _WRAP_CENTER

    _set_widths(
        ws,
        [_STAFF_COLUMN_WIDTH, _ROLE_COLUMN_WIDTH]
        + [_DATE_COLUMN_WIDTH] * len(dates)
        + [10, _REMARKS_COLUMN_WIDTH],
    )
    _setup_print(ws, freeze=f"C{status_row + 1}")


# ---------------------------------------------------------------------------
# Sheet 2: 日別確認
# ---------------------------------------------------------------------------


def _build_daily_sheet(wb: Workbook, export: ScheduleExport) -> None:
    ws = wb.create_sheet(SHEET_DAILY)
    role_ids = list(export.role_names)
    headers = ["日付", "状態", "判定", "予約室数", "必要人数", "配置人数", "人数"]
    headers += [export.role_names[r] for r in role_ids]
    headers += ["スキル条件"]
    write_header_row(ws, 1, headers)

    for row, day in enumerate(export.validation.days, start=2):
        values = [
            day.work_date,
            format_schedule_status(day),
            format_day_state(day),
            format_reserved_rooms(day),
            format_required(day),
            format_scheduled(day),
            format_staff_count(day),
        ]
        values += [format_role(day, r) for r in role_ids]
        values += [format_skill(day)]
        fill = weekday_fill(day.work_date)
        for col, value in enumerate(values, start=1):
            set_cell(ws, row, col, value, fill=fill if col == 1 else None, align_center=True)

    ws.freeze_panes = "B2"
    _set_widths(ws, [12, 10, 16, 10, 10, 10, 12] + [16] * len(role_ids) + [20])
    _setup_print(ws, freeze="B2")


# ---------------------------------------------------------------------------
# Sheet 3: 問題一覧
# ---------------------------------------------------------------------------


def _build_issues_sheet(wb: Workbook, export: ScheduleExport) -> None:
    ws = wb.create_sheet(SHEET_ISSUES)
    write_header_row(ws, 1, ["日付", "Severity", "Issue Code", "内容"])
    issues = sorted(export.validation.issues, key=severity_sort_key)
    for row, issue in enumerate(issues, start=2):
        set_cell(ws, row, 1, issue.work_date, align_center=True)
        set_cell(
            ws, row, 2,
            SEVERITY_LABELS.get(issue.severity, issue.severity),
            bold=True, align_center=True,
        )
        set_cell(ws, row, 3, issue.code)
        set_cell(ws, row, 4, issue.message)
    if not issues:
        set_cell(ws, 2, 4, "問題は見つかりませんでした。")
    ws.freeze_panes = "A2"
    _set_widths(ws, [12, 10, 24, 90])


# ---------------------------------------------------------------------------
# Sheet 4: 出力情報
# ---------------------------------------------------------------------------


def _build_info_sheet(wb: Workbook, export: ScheduleExport, exported_at: str) -> None:
    ws = wb.create_sheet(SHEET_INFO)
    validation = export.validation
    dates = export.work_dates
    ws.cell(row=1, column=1, value=f"{APP_NAME} 出力情報").font = TITLE_FONT

    items = [
        ("アプリ名", APP_NAME),
        ("対象期間", f"{dates[0]} 〜 {dates[-1]}" if dates else "-"),
        ("対象日数", len(dates)),
        ("作成済み日数", validation.existing_days),
        ("未作成日数", validation.missing_days),
        ("確定日数", validation.finalized_days),
        ("下書き日数", validation.draft_days),
        ("問題なし日数", validation.clear_days),
        ("問題あり日数", validation.problem_days),
        ("要件未設定日数", validation.requirement_missing_days),
        ("出力日時", exported_at),
    ]
    write_header_row(ws, 3, ["項目", "値"])
    for row, (label, value) in enumerate(items, start=4):
        set_cell(ws, row, 1, label, bold=True)
        set_cell(ws, row, 2, value)
    _set_widths(ws, [22, 40])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_schedule_workbook(export: ScheduleExport) -> Workbook:
    """勤務表・日別確認・問題一覧・出力情報の4シートを持つワークブックを作る.

    不足がある勤務表でも出力する（不足している状態を確認・共有したい場合があるため）。
    """
    exported_at = export.exported_at or datetime.now().isoformat(timespec="seconds")
    wb = new_workbook()
    _build_schedule_sheet(wb, export)
    _build_daily_sheet(wb, export)
    _build_issues_sheet(wb, export)
    _build_info_sheet(wb, export, exported_at)
    return wb


def build_schedule_excel(export: ScheduleExport) -> bytes:
    """st.download_button 向けに .xlsx の bytes を返す（メモリ上で生成する）."""
    return workbook_to_bytes(build_schedule_workbook(export))
