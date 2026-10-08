"""通常勤務時刻（HH:MM）の解析と標準勤務分の計算.

勤怠CSVの勤務セル（attendance_import.parse_time_range）とは別物として扱う。
勤怠CSVは翌日跨ぎ（例 23:00-30:00）を許容するが、清掃スタッフの通常勤務マスターでは
00:00〜23:59 の範囲のみを受け付け、終了 > 開始 を必須とする。

standard_work_minutes はDBへ保存せず、必要なときに開始・終了から計算する
（同じ値を二重に持つと片方だけ更新される事故が起きるため）。
"""

import re

from src.constants import STANDARD_TIME_HOUR_MAX

_HHMM_PATTERN = re.compile(r"^(\d{1,2}):(\d{2})$")


def parse_hhmm(text: object) -> int | None:
    """'HH:MM' を0時起点の分に変換する. 形式・範囲外はNone（例外は投げない）.

    許容範囲は 00:00〜23:59。'9:00' のような1桁時も受け付ける。
    """
    if not isinstance(text, str):
        return None
    match = _HHMM_PATTERN.match(text.strip())
    if match is None:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if not 0 <= hour <= STANDARD_TIME_HOUR_MAX:
        return None
    if not 0 <= minute <= 59:
        return None
    return hour * 60 + minute


def is_valid_hhmm(text: object) -> bool:
    return parse_hhmm(text) is not None


def normalize_hhmm(text: object) -> str | None:
    """'9:00' を '09:00' に正規化する. 不正な値はNone."""
    minutes = parse_hhmm(text)
    if minutes is None:
        return None
    return format_minutes(minutes)


def format_minutes(minutes: int) -> str:
    """0時起点の分を 'HH:MM' にする."""
    hour, minute = divmod(minutes, 60)
    return f"{hour:02d}:{minute:02d}"


def standard_work_minutes(start_time: object, end_time: object) -> int | None:
    """通常勤務時間の分数. どちらか未設定・不正・終了<=開始の場合はNone."""
    start = parse_hhmm(start_time)
    end = parse_hhmm(end_time)
    if start is None or end is None or end <= start:
        return None
    return end - start


def format_standard_work_time(start_time: object, end_time: object) -> str:
    """一覧・確認用の表示文字列（例 '09:00-15:30（390分）'）.

    未設定なら空文字。片方のみ設定や不正な組み合わせは入力値をそのまま見せて
    「設定が不完全であること」が画面で分かるようにする。
    """
    start = normalize_hhmm(start_time)
    end = normalize_hhmm(end_time)
    if start is None and end is None:
        return ""
    minutes = standard_work_minutes(start_time, end_time)
    if minutes is None:
        return f"{start or '?'}-{end or '?'}"
    return f"{start}-{end}（{minutes}分）"
