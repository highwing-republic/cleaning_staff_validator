"""Streamlit画面共通のヘルパー.

業務ロジックは持たない。DB接続・月選択・表示補助のみを提供する。
"""

import os
import sqlite3
from datetime import date

import streamlit as st

from src.constants import SKILL_LEVELS
from src.database import get_connection, initialize_database
from src.models import ValidationError

# 元アプリ（STAFF_SHIFT_DB_PATH）と同じDBへ接続しないよう専用の変数名を使う
DB_PATH_ENV = "CLEANING_STAFF_VALIDATOR_DB_PATH"

WEEKDAY_LABELS_JA = ("月", "火", "水", "木", "金", "土", "日")


def format_skill_level(level: int) -> str:
    """スキルレベルの表示ラベルを作る（例: "3 - 標準"）."""
    return f"{level} - {SKILL_LEVELS.get(level, '?')}"


def format_weekdays(weekdays: list[int] | tuple[int, ...]) -> str:
    """通常勤務曜日を人が読める形にする（例 '月・火・木・金・土'）.

    DBの内部値（0,1,3,4,5）をそのまま画面に出さないための変換。
    """
    labels = [
        WEEKDAY_LABELS_JA[w]
        for w in sorted(set(weekdays))
        if isinstance(w, int) and 0 <= w < len(WEEKDAY_LABELS_JA)
    ]
    return "・".join(labels)


def format_optional_int(value: int | None, suffix: str = "") -> str:
    """任意項目の数値表示. 未設定は「未設定」と出す（空欄だと入力漏れと区別できない）."""
    if value is None:
        return "未設定"
    return f"{value}{suffix}"


def open_connection() -> sqlite3.Connection:
    """このスクリプト実行用のDB接続を新規に作る.

    sqlite3の接続はStreamlitのスレッド間で共有してはいけないため、
    st.cache_resourceは使わずスクリプト実行のたびに新しい接続を作る。
    """
    db_path = os.environ.get(DB_PATH_ENV) or None
    conn = get_connection(db_path)
    initialize_database(conn)
    return conn


def _default_year_month() -> str:
    today = date.today()
    year = today.year
    month = today.month + 1
    if month > 12:
        month -= 12
        year += 1
    return f"{year:04d}-{month:02d}"


def _shift_year_month(year_month: str, offset: int) -> str:
    year, month = (int(p) for p in year_month.split("-"))
    total = year * 12 + (month - 1) + offset
    year, month = divmod(total, 12)
    return f"{year:04d}-{month + 1:02d}"


def select_year_month(key: str = "year_month") -> str:
    """対象年月セレクタ（T64）. st.session_stateで画面間共有する.

    既定値は翌月。-6か月〜+12か月の範囲を選択肢にする。
    """
    if key not in st.session_state:
        st.session_state[key] = _default_year_month()

    base = _default_year_month()
    options = [_shift_year_month(base, offset) for offset in range(-6, 13)]
    current = st.session_state[key]
    if current not in options:
        options = sorted(set(options) | {current})

    selected = st.selectbox(
        "対象年月",
        options=options,
        index=options.index(current),
        format_func=format_year_month_ja,
        key=f"_select_{key}",
    )
    st.session_state[key] = selected
    return selected


def format_year_month_ja(year_month: str) -> str:
    """'YYYY-MM' を '2026年9月' のように表示する."""
    year, month = year_month.split("-")
    return f"{year}年{int(month)}月"


def format_date_ja(work_date: str) -> str:
    """'YYYY-MM-DD' を '10月5日(月)' のように表示する."""
    from src.month_utils import weekday_index

    _, month, day = work_date.split("-")
    wd = WEEKDAY_LABELS_JA[weekday_index(work_date)]
    return f"{int(month)}月{int(day)}日({wd})"


def show_errors(errors: list[ValidationError]) -> None:
    """検証エラー一覧をst.errorで表示する."""
    for err in errors:
        parts = []
        if err.work_date:
            parts.append(format_date_ja(err.work_date))
        if err.staff_id is not None:
            parts.append(f"スタッフID {err.staff_id}")
        prefix = "／".join(parts)
        text = f"[{prefix}] {err.message}" if prefix else err.message
        st.error(text)

