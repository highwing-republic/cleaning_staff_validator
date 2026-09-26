"""勤怠CSV取込画面（Phase 2で実装予定のプレースホルダ）."""

import streamlit as st

from src import repositories as repo
from src.ui_common import open_connection, select_year_month

st.set_page_config(page_title="勤怠CSV取込", layout="wide")
st.title("② 勤怠CSV取込")
st.info("勤怠CSVの取込機能は準備中です（Phase 2で実装予定）。")

conn = open_connection()
year_month = select_year_month()

active = repo.get_active_import(conn, year_month)
if active is None:
    st.caption("この月の取込データはまだありません。")
else:
    st.caption(f"現在の取込: {active.source_filename}（{active.imported_at}）")
