"""希望休入力画面（T66〜T67）."""

import pandas as pd
import streamlit as st

from src import repositories as repo
from src.constants import (
    PREFERENCE_PREFER_OFF,
    PREFERENCE_PREFER_WORK,
    PREFERENCE_UNAVAILABLE,
)
from src.models import PreferenceInput
from src.month_utils import get_month_dates, weekday_index
from src.ui_common import WEEKDAY_LABELS_JA, open_connection, select_year_month
from src.validation import validate_preference

st.set_page_config(page_title="希望休入力", layout="wide")
st.title("③ 希望休入力")

conn = open_connection()

year_month = select_year_month()

LABEL_NONE = "指定なし"
LABEL_UNAVAILABLE = "絶対休み"
LABEL_PREFER_OFF = "できれば休み"
LABEL_PREFER_WORK = "できれば勤務"

LABEL_TO_TYPE = {
    LABEL_NONE: None,
    LABEL_UNAVAILABLE: PREFERENCE_UNAVAILABLE,
    LABEL_PREFER_OFF: PREFERENCE_PREFER_OFF,
    LABEL_PREFER_WORK: PREFERENCE_PREFER_WORK,
}
TYPE_TO_LABEL = {v: k for k, v in LABEL_TO_TYPE.items()}

staff_list = repo.list_staff(conn, include_inactive=False)
if not staff_list:
    st.info("有効なスタッフが登録されていません。先にスタッフを登録してください。")
    st.stop()

dates = get_month_dates(year_month)

st.caption(
    "凡例: 指定なし / 絶対休み(Hard) / できれば休み(Soft) / できれば勤務(Soft)。"
    "通常勤務不可曜日には「できれば勤務」を指定できません（保存時にエラー表示）。"
)

with st.expander("通常勤務不可曜日の凡例"):
    for s in staff_list:
        unavailable_wd = [
            WEEKDAY_LABELS_JA[wd] for wd in range(7) if not s.weekday_availability.get(wd, False)
        ]
        if unavailable_wd:
            st.write(f"{s.staff_name}さん: {''.join(unavailable_wd)} (勤務不可曜日)")

existing_by_key = {
    (p.staff_id, p.work_date): p.preference_type for p in repo.get_preferences(conn, year_month)
}


def _col_name(work_date: str) -> str:
    _, month, day = work_date.split("-")
    wd = WEEKDAY_LABELS_JA[weekday_index(work_date)]
    return f"{int(month)}/{int(day)}({wd})"

date_by_col = {_col_name(d): d for d in dates}

rows = []
for s in staff_list:
    row = {"staff_id": s.staff_id, "スタッフ名": s.staff_name}
    for d in dates:
        row[_col_name(d)] = TYPE_TO_LABEL.get(existing_by_key.get((s.staff_id, d)), LABEL_NONE)
    rows.append(row)

df = pd.DataFrame(rows)

column_config = {
    "staff_id": None,
    "スタッフ名": st.column_config.TextColumn(disabled=True),
}
for col in date_by_col:
    column_config[col] = st.column_config.SelectboxColumn(
        options=[LABEL_NONE, LABEL_UNAVAILABLE, LABEL_PREFER_OFF, LABEL_PREFER_WORK],
        required=True,
    )

edited = st.data_editor(
    df,
    hide_index=True,
    width="stretch",
    disabled=["staff_id", "スタッフ名"],
    column_config=column_config,
    key="preferences_editor",
)

if st.button("保存", type="primary"):
    staff_by_id = {s.staff_id: s for s in staff_list}
    to_save: list[PreferenceInput] = []
    to_delete: list[tuple[int, str]] = []
    all_errors = []

    for _, row in edited.iterrows():
        staff_id = int(row["staff_id"])
        staff = staff_by_id[staff_id]
        for col, work_date in date_by_col.items():
            label = row[col]
            new_type = LABEL_TO_TYPE[label]
            old_type = existing_by_key.get((staff_id, work_date))
            if new_type == old_type:
                continue
            if new_type is None:
                to_delete.append((staff_id, work_date))
                continue
            pref = PreferenceInput(staff_id, work_date, new_type)
            errors = validate_preference(pref, staff)
            if errors:
                for e in errors:
                    all_errors.append((staff.staff_name, work_date, e))
            else:
                to_save.append(pref)

    if all_errors:
        for name, work_date, e in all_errors:
            st.error(f"{name}さん {work_date}: {e.message}")
    elif not to_save and not to_delete:
        st.info("変更はありません。")
    else:
        for staff_id, work_date in to_delete:
            repo.delete_preference(conn, staff_id, work_date)
        for pref in to_save:
            repo.save_preference(conn, pref)
        st.success("保存しました。")
        st.rerun()
