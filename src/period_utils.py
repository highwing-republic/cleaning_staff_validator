"""対象期間（10〜14日程度のローリング期間）のユーティリティ.

現場は月単位ではなく「今日から10〜14日先まで」を単位にシフトを組むため、
year_month ではなく開始日＋日数で期間を表す。
期間そのものはDBに保存せず、勤務希望は日付単位で保持する
（次回の期間が前回と重なっても希望データを動かさずに済むようにするため）。

今日に依存する処理は default_period_start に閉じ込める（テストを安定させるため）。
"""

from datetime import date, timedelta

# 若女将が一度に組むのは10〜14日程度。それ以上は画面が重くなり転記もしづらい
PERIOD_MIN_DAYS = 1
PERIOD_MAX_DAYS = 14
PERIOD_DEFAULT_DAYS = 14
# 画面の選択肢
PERIOD_DAY_OPTIONS: tuple[int, ...] = (10, 11, 12, 13, 14)


def parse_date(work_date: object) -> date | None:
    """'YYYY-MM-DD' を date にする. 形式・値が不正ならNone（例外は投げない）."""
    if isinstance(work_date, date):
        return work_date
    if not isinstance(work_date, str):
        return None
    text = work_date.strip()
    if len(text) != 10:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def is_valid_date(work_date: object) -> bool:
    return parse_date(work_date) is not None


def period_dates(start_date: str | date, days: int) -> list[str]:
    """開始日から days 日分の 'YYYY-MM-DD' を昇順で返す（月をまたげる）.

    days が 1〜PERIOD_MAX_DAYS の範囲外、開始日が不正な場合は ValueError。
    """
    start = parse_date(start_date)
    if start is None:
        raise ValueError(f"start_date must be YYYY-MM-DD: {start_date!r}")
    if not isinstance(days, int) or isinstance(days, bool):
        raise ValueError(f"days must be int: {days!r}")
    if not PERIOD_MIN_DAYS <= days <= PERIOD_MAX_DAYS:
        raise ValueError(f"days must be {PERIOD_MIN_DAYS}..{PERIOD_MAX_DAYS}: {days!r}")
    return [(start + timedelta(days=offset)).isoformat() for offset in range(days)]


def period_end_date(start_date: str | date, days: int) -> str:
    """期間の最終日. period_dates と同じ検証を行う."""
    return period_dates(start_date, days)[-1]


def default_period_start(today: date | None = None) -> str:
    """既定の開始日（翌日）. 今日への依存はこの関数だけに閉じ込める."""
    base = today or date.today()
    return (base + timedelta(days=1)).isoformat()


def format_period(start_date: str, days: int) -> str:
    """'2026-10-20 ～ 2026-11-02（14日間）' のような表示文字列."""
    dates = period_dates(start_date, days)
    return f"{dates[0]} ～ {dates[-1]}（{days}日間）"
