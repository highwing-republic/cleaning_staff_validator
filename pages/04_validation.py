"""日別検証画面（Phase 2で実装予定のプレースホルダ）.

Excel出力もこの画面に置く予定。
"""

import streamlit as st

from src.ui_common import open_connection, select_year_month

st.set_page_config(page_title="日別検証", layout="wide")
st.title("④ 日別検証")
st.info("日別検証・Excel出力は準備中です（Phase 2で実装予定）。")

conn = open_connection()
select_year_month()
