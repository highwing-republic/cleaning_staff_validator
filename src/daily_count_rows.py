"""勤務表の表の先頭に出す「出勤人数」「必要人数」の行（かんたん作成の③④）.

スタッフ×日付の表だけだと、日ごとに何人出勤しているかを数えないと分からない。
表の先頭に日ごとの出勤人数と必要人数を並べ、不足・多すぎる日は文字と色で示す
（色が分からなくても読めるよう、「1人不足」のように文字でも書く）。

表の組み立ては Streamlit に依存しない関数にしてある。
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass

import pandas as pd

from src.models import ScheduleGenerationResult, SchedulePeriodValidationResult

STAFF_COLUMN = "スタッフ"
DAYS_COLUMN = "勤務日数"
WORKING_LABEL = "【出勤人数】"
REQUIRED_LABEL = "【必要人数】"
NOT_CREATED = "未作成"
NOT_SET = "未設定"

# 文字色も指定する（暗い表示設定でも読めるように）
SUMMARY_STYLE = "background-color: #e8f1f6; color: #2b3440"  # 見た目（くすんだ青）に合わせた薄い青
SHORTAGE_STYLE = "background-color: #fde2e1; color: #8a1c1c"
EXCESS_STYLE = "background-color: #fff3cd; color: #6b4e00"


@dataclass(frozen=True)
class DayCount:
    """1日分の出勤人数と必要人数. working=None は勤務表が未作成、required=None は必要人数が未設定."""

    working: int | None
    required: int | None = None
    maximum: int | None = None

    @property
    def shortage(self) -> int:
        if self.working is None or self.required is None:
            return 0
        return max(0, self.required - self.working)

    @property
    def excess(self) -> int:
        if self.working is None or self.maximum is None:
            return 0
        return max(0, self.working - self.maximum)


def working_cell(count: DayCount) -> str:
    """'5人' / '4人（1人不足）' / '8人（1人多い）' / '未作成'."""
    if count.working is None:
        return NOT_CREATED
    text = f"{count.working}人"
    if count.shortage:
        return f"{text}（{count.shortage}人不足）"
    if count.excess:
        return f"{text}（{count.excess}人多い）"
    return text


def required_cell(count: DayCount) -> str:
    """'5人' / '5〜7人'（最大人数あり）/ '未設定'."""
    if count.required is None:
        return NOT_SET
    if count.maximum is not None and count.maximum != count.required:
        return f"{count.required}〜{count.maximum}人"
    return f"{count.required}人"


def counts_from_validation(validation: SchedulePeriodValidationResult) -> dict[str, DayCount]:
    """保存済みの勤務表の検証結果から、日ごとの人数を取り出す."""
    counts: dict[str, DayCount] = {}
    for day in validation.days:
        maximum = day.requirement.max_total_staff if day.requirement else None
        counts[day.work_date] = DayCount(
            working=day.scheduled_staff if day.exists else None,
            required=day.required_staff,
            maximum=maximum,
        )
    return counts


def counts_from_generation(
    result: ScheduleGenerationResult, maximums: dict[str, int | None] | None = None
) -> dict[str, DayCount]:
    """新しい勤務案（まだ保存していない）から、日ごとの人数を取り出す."""
    maximums = maximums or {}
    return {
        day.work_date: DayCount(
            working=day.scheduled_staff_count,
            required=day.required_total_staff if day.requirement_is_set else None,
            maximum=maximums.get(day.work_date),
        )
        for day in result.days
    }


def build_schedule_table(
    staff_rows: Iterable[dict],
    dates: list[str],
    counts: dict[str, DayCount],
    date_label: Callable[[str], str],
) -> pd.DataFrame:
    """先頭に「出勤人数」「必要人数」の行を付けた勤務表.

    staff_rows は {スタッフ, 日付の表示名..., 勤務日数} の辞書。
    人数が分からない日（counts に無い日）は空欄にする。
    """
    def cell(work_date: str, formatter: Callable[[DayCount], str]) -> str:
        count = counts.get(work_date)
        return formatter(count) if count is not None else ""

    summary = [
        {STAFF_COLUMN: WORKING_LABEL,
         **{date_label(d): cell(d, working_cell) for d in dates},
         DAYS_COLUMN: ""},
        {STAFF_COLUMN: REQUIRED_LABEL,
         **{date_label(d): cell(d, required_cell) for d in dates},
         DAYS_COLUMN: ""},
    ]
    table = pd.DataFrame([*summary, *staff_rows])
    # 勤務日数は人数の行では空欄にするため、文字列の列としてそろえる
    table[DAYS_COLUMN] = table[DAYS_COLUMN].map(lambda v: "" if v == "" else f"{v}日")
    return table


def table_styles(
    table: pd.DataFrame,
    dates: list[str],
    counts: dict[str, DayCount],
    date_label: Callable[[str], str],
) -> pd.DataFrame:
    """Styler 用の色. 人数の行を薄い灰色にし、不足は赤・多すぎは黄色にする."""
    styles = pd.DataFrame("", index=table.index, columns=table.columns)
    summary_rows = table.index[table[STAFF_COLUMN].isin([WORKING_LABEL, REQUIRED_LABEL])]
    styles.loc[summary_rows, :] = SUMMARY_STYLE
    working_row = table.index[table[STAFF_COLUMN] == WORKING_LABEL]
    for work_date in dates:
        count = counts.get(work_date)
        if count is None:
            continue
        if count.shortage:
            styles.loc[working_row, date_label(work_date)] = SHORTAGE_STYLE
        elif count.excess:
            styles.loc[working_row, date_label(work_date)] = EXCESS_STYLE
    return styles
