"""Streamlit画面共通のヘルパー（T60〜T84のUI共通処理）.

業務ロジックは持たない。DB接続・月選択・表示補助のみを提供する。
"""

import os
import sqlite3
from datetime import date

import streamlit as st

from src.constants import SKILL_LEVELS
from src.database import get_connection, initialize_database
from src.models import ValidationError

DB_PATH_ENV = "STAFF_SHIFT_DB_PATH"

WEEKDAY_LABELS_JA = ("月", "火", "水", "木", "金", "土", "日")


def format_skill_level(level: int) -> str:
    """スキルレベルの表示ラベルを作る（例: "3 - 標準"）."""
    return f"{level} - {SKILL_LEVELS.get(level, '?')}"


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

    def _label(ym: str) -> str:
        year, month = ym.split("-")
        return f"{year}年{int(month)}月"

    selected = st.selectbox(
        "対象年月",
        options=options,
        index=options.index(current),
        format_func=_label,
        key=f"_select_{key}",
    )
    st.session_state[key] = selected
    return selected


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


def confirmed_banner(conn: sqlite3.Connection, year_month: str) -> bool:
    """対象月が確定済みならst.infoを表示してTrueを返す."""
    from src import services

    if services.is_confirmed(conn, year_month):
        st.info("この月は確定済みです（編集不可）")
        return True
    return False
