"""Standalone printable HTML for a generated cleaning schedule."""

from datetime import datetime
from html import escape
import re

from src.models import (
    ScheduleDayValidationResult,
    ScheduleExport,
    ScheduleExportStaffRow,
)
from src.jp_holidays import DAY_COLOR_HOLIDAY, DAY_COLOR_SATURDAY, day_color
from src.monthly_hours import HOURS_NOTE, format_hours, month_column
from src.period_utils import format_date_short
from src.schedule_output_display import (
    DRAFT_NOTICE,
    STATUS_MISSING_LABEL,
    SEVERITY_TEXTS,
    format_date_group,
    format_day_state,
    format_required,
    format_schedule_status,
    format_scheduled,
    format_staff_count,
    group_issues,
)

APP_NAME = "清掃スタッフ勤務表"
REMARKS_HEADER = "当日変更・備考"
_SHIFT_TIME = re.compile(r"^(\d{1,2}:\d{2})-(\d{1,2}:\d{2})$")


def _e(value: object) -> str:
    """Escape all values interpolated into HTML text or attributes."""
    return escape(str(value), quote=True)


def _schedule_cell(row: ScheduleExportStaffRow, work_date: str, day: ScheduleDayValidationResult | None) -> str:
    if day is None or not day.exists:
        return STATUS_MISSING_LABEL
    return row.cells.get(work_date, "-")


def _render_shift_cell(value: str) -> str:
    """Render a normal shift time on two lines; all other values as escaped text."""
    match = _SHIFT_TIME.fullmatch(value)
    if match:
        return (
            f'<span class="shift-time">{_e(match.group(1))}</span>'
            f'<span class="shift-time">{_e(match.group(2))}</span>'
        )
    return _e(value)


def _issue_details(export: ScheduleExport) -> str:
    # 同じ内容は日付をまとめて1行にする（14日分の「下書きです」を14行並べない）
    groups = group_issues(export.validation.issues)
    if not groups:
        return ""
    rows = "".join(
        "<li>"
        f"{_e(format_date_group(group.work_dates))}　"
        f"{_e(SEVERITY_TEXTS.get(group.severity, group.severity))}："
        f"{_e(group.message)}"
        "</li>"
        for group in groups
    )
    return f'<section class="issue-details"><h2>確認が必要な内容</h2><ul>{rows}</ul></section>'


def _day_class(work_date: str) -> str:
    """日付見出しの色分け（土曜は青、日曜・祝日は赤）. 平日は class を付けない."""
    color = day_color(work_date)
    if color == DAY_COLOR_SATURDAY:
        return ' class="day-sat"'
    if color == DAY_COLOR_HOLIDAY:
        return ' class="day-holiday"'
    return ""


def _daily_table(export: ScheduleExport) -> str:
    rows = []
    for day in export.validation.days:
        rows.append(
            "<tr>"
            f"<th scope=\"row\">{_e(format_date_short(day.work_date))}</th>"
            f"<td>{_e(format_schedule_status(day))}</td>"
            f"<td>{_e(format_day_state(day))}</td>"
            f"<td>{_e(format_scheduled(day))}</td>"
            f"<td>{_e(format_required(day))}</td>"
            f"<td>{_e(format_staff_count(day))}</td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="6">対象日がありません</td></tr>')
    return (
        '<table class="daily-table"><thead><tr><th>日付</th><th>勤務表</th>'
        '<th>確認状況</th><th>配置人数</th><th>必要人数</th><th>人数判定</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _month_total_cell(total) -> str:
    """'110.5時間' と '17日' を2行で（狭い列でも読めるように）. 勤務がなければ '0時間'."""
    if total is None or total.days == 0:
        return '<td class="month-total">0時間</td>'
    return (
        '<td class="month-total">'
        f'<span class="shift-time">{_e(format_hours(total.minutes))}</span>'
        f'<span class="shift-time">{total.days}日</span></td>'
    )


def build_schedule_print_html(export: ScheduleExport) -> str:
    """Return a self-contained, accessible HTML page for printing the schedule."""
    dates = export.work_dates
    period = f"{dates[0]} ～ {dates[-1]}" if dates else "対象期間なし"
    created_at = export.exported_at or datetime.now().isoformat(timespec="seconds")
    headers = ''.join(
        f'<th scope="col"{_day_class(date)}>{_e(format_date_short(date))}</th>' for date in dates
    )
    months = list(export.months)
    month_headers = ''.join(f'<th scope="col" class="month-total">{_e(month_column(m))}</th>' for m in months)
    staff_rows = []
    for row in export.staff_rows:
        cells = ''.join(
            f'<td class="shift">{_render_shift_cell(_schedule_cell(row, date, export.validation.day(date)))}</td>'
            for date in dates
        )
        month_cells = ''.join(_month_total_cell(row.month_totals.get(m)) for m in months)
        staff_rows.append(
            '<tr>'
            f'<th scope="row" class="staff-name">{_e(row.staff.staff_name)}</th>'
            f'<td>{_e(row.role_name)}</td>{cells}{month_cells}'
            '<td class="remarks-cell" aria-label="手書き用の備考欄"></td>'
            '</tr>'
        )
    if not staff_rows:
        staff_rows.append(f'<tr><td colspan="{len(dates) + len(months) + 3}">スタッフが登録されていません</td></tr>')
    hours_note = (
        f'<p class="hours-note">「{"」「".join(month_column(m) for m in months)}」は、'
        f'その月に保存した勤務の合計です（{_e(HOURS_NOTE)}）。</p>'
        if months else ""
    )

    notices = []
    if export.validation.has_draft:
        notices.append(f'<p class="notice">{_e(DRAFT_NOTICE)} 確定前の内容を含みます。配布前に確認してください。</p>')
    if export.validation.problem_days:
        notices.append(f'<p class="notice problem">確認が必要な日があります（{export.validation.problem_days}日）。日別の確認状況をご覧ください。</p>')
    if export.validation.missing_days:
        notices.append(f'<p class="notice">未作成の日があります（{export.validation.missing_days}日）。</p>')
    notice_html = ''.join(notices)

    return f'''<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(APP_NAME)} - {_e(period)}</title>
<style>
@page {{ size: A4 landscape; margin: 10mm; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; color: #17212b; font-family: "Yu Gothic", "Meiryo", sans-serif; font-size: 12px; }}
.toolbar {{ padding: 12px 16px; background: #f1f5f9; }}
.print-button {{ border: 0; border-radius: 6px; padding: 10px 18px; background: #3b7ea1; color: white; font-size: 16px; cursor: pointer; }}
main {{ padding: 12px; }}
h1 {{ margin: 0 0 4px; font-size: 22px; }}
.meta {{ margin: 3px 0; }}
.notice {{ margin: 8px 0; padding: 7px 9px; border: 1px solid #a56a00; background: #fff8e6; font-weight: bold; }}
.problem {{ border-color: #a52323; background: #fff0f0; }}
table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
th, td {{ border: 1px solid #66717c; padding: 4px 3px; text-align: center; vertical-align: middle; overflow-wrap: anywhere; }}
thead {{ display: table-header-group; }}
th {{ background: #e8edf2; }}
.schedule-table {{ margin-top: 9px; font-size: 10px; }}
.schedule-table th:first-child {{ width: 115px; }}
.schedule-table th:nth-child(2) {{ width: 78px; }}
.schedule-table th:last-child {{ width: 120px; }}
.staff-name {{ text-align: left; }}
.schedule-table th.month-total {{ width: 62px; }}
.month-total {{ white-space: nowrap; }}
.hours-note {{ margin: 4px 0 0; font-size: 10px; }}
.day-sat {{ color: #1457a6; background: #e3edf9; }}
.day-holiday {{ color: #b42318; background: #fbe7e6; }}
.shift {{ white-space: normal; }}
.shift-time {{ display: block; white-space: nowrap; }}
.remarks-cell {{ min-width: 100px; }}
tr {{ break-inside: avoid; page-break-inside: avoid; }}
.daily-section {{ margin-top: 14px; break-inside: avoid; }}
h2 {{ margin: 10px 0 5px; font-size: 15px; }}
.daily-table {{ max-width: 650px; }}
.daily-table th:first-child {{ width: 85px; }}
.daily-table td:nth-child(3) {{ text-align: left; }}
.issue-details {{ margin-top: 10px; break-inside: avoid; }}
.issue-details ul {{ margin: 4px 0; padding-left: 22px; }}
.issue-details li {{ margin: 2px 0; }}
.created {{ margin-top: 10px; font-size: 10px; }}
@media screen {{
  .schedule-table {{ min-width: 1000px; }}
  .daily-table {{ min-width: 600px; }}
}}
@media print {{
  .toolbar {{ display: none !important; }}
  main {{ padding: 0; }}
  body {{ print-color-adjust: exact; -webkit-print-color-adjust: exact; }}
}}
</style>
</head>
<body>
<div class="toolbar"><button class="print-button" type="button" onclick="window.print()">印刷する</button></div>
<main>
<h1>{_e(APP_NAME)}</h1>
<p class="meta">勤務表名：清掃勤務表</p>
<p class="meta">対象期間：{_e(period)}</p>
{notice_html}
<table class="schedule-table"><thead><tr><th scope="col">スタッフ</th><th scope="col">役割</th>{headers}{month_headers}<th scope="col">{_e(REMARKS_HEADER)}</th></tr></thead>
<tbody>{''.join(staff_rows)}</tbody></table>
{hours_note}
<section class="daily-section"><h2>日別の配置と必要人数</h2>{_daily_table(export)}</section>
{_issue_details(export)}
<p class="created">作成時刻：{_e(created_at)}</p>
</main>
</body>
</html>'''
