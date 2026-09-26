"""スタッフ管理画面（T60〜T63）."""

import pandas as pd
import streamlit as st

from src import repositories as repo
from src.constants import SKILL_LEVEL_DEFAULT, SKILL_LEVEL_MAX, SKILL_LEVEL_MIN
from src.ui_common import WEEKDAY_LABELS_JA, format_skill_level, open_connection
from src.validation import validate_staff

st.set_page_config(page_title="スタッフ管理", layout="wide")
st.title("① スタッフ管理")

conn = open_connection()


def _role_options() -> list[dict]:
    return repo.list_roles(conn)


def _weekday_summary(staff) -> str:
    labels = [
        WEEKDAY_LABELS_JA[wd] for wd in range(7) if staff.weekday_availability.get(wd, False)
    ]
    return "".join(labels) if labels else "(なし)"


roles = _role_options()
role_name_by_id = {r["role_id"]: r["role_name"] for r in roles}

# ---------------------------------------------------------------------------
# 一覧
# ---------------------------------------------------------------------------

st.subheader("一覧")
include_inactive = st.toggle("無効スタッフも表示する", value=True)
staff_list = repo.list_staff(conn, include_inactive=include_inactive)

if staff_list:
    table = pd.DataFrame(
        [
            {
                "ID": s.staff_id,
                "名前": s.staff_name,
                "ロール": role_name_by_id.get(s.role_id, s.role_id),
                "1日勤務(分)": s.daily_work_minutes,
                "最大連勤": s.max_consecutive_days,
                "スキル": format_skill_level(s.skill_level),
                "有効": "有効" if s.active else "無効",
                "勤務可能曜日": _weekday_summary(s),
            }
            for s in staff_list
        ]
    )
    st.dataframe(table, width="stretch", hide_index=True)
else:
    st.info("スタッフが登録されていません。")

# ---------------------------------------------------------------------------
# 新規作成
# ---------------------------------------------------------------------------

st.subheader("新規スタッフ登録")
with st.form("create_staff_form", clear_on_submit=True):
    name = st.text_input("スタッフ名")
    role_id = st.selectbox(
        "ロール", options=[r["role_id"] for r in roles],
        format_func=lambda rid: role_name_by_id.get(rid, str(rid)),
    )
    daily_minutes = st.number_input("1日の勤務時間（分）", min_value=1, max_value=1440, value=480, step=15)
    max_consecutive = st.number_input("最大連続勤務日数", min_value=1, value=5, step=1)
    skill_level = st.selectbox(
        "スキル",
        options=list(range(SKILL_LEVEL_MIN, SKILL_LEVEL_MAX + 1)),
        index=SKILL_LEVEL_DEFAULT - SKILL_LEVEL_MIN,
        format_func=format_skill_level,
    )
    submitted = st.form_submit_button("登録")

    if submitted:
        errors = validate_staff(name, int(daily_minutes), int(max_consecutive), int(skill_level))
        if errors:
            for e in errors:
                st.error(e.message)
        else:
            new_id = repo.create_staff(
                conn, name.strip(), role_id, int(daily_minutes), int(max_consecutive), int(skill_level)
            )
            st.success(f"スタッフ「{name.strip()}」を登録しました（ID: {new_id}）。")
            st.rerun()

# ---------------------------------------------------------------------------
# 編集・無効化・勤務可能曜日
# ---------------------------------------------------------------------------

st.subheader("編集 / 勤務可能曜日")

if not staff_list:
    st.stop()

edit_target = st.selectbox(
    "編集するスタッフ",
    options=[s.staff_id for s in staff_list],
    format_func=lambda sid: next(s.staff_name for s in staff_list if s.staff_id == sid),
    key="edit_target_staff",
)
target = repo.get_staff(conn, edit_target)

with st.form("edit_staff_form"):
    edit_name = st.text_input("スタッフ名", value=target.staff_name)
    edit_role_id = st.selectbox(
        "ロール",
        options=[r["role_id"] for r in roles],
        index=[r["role_id"] for r in roles].index(target.role_id),
        format_func=lambda rid: role_name_by_id.get(rid, str(rid)),
        key="edit_role_id",
    )
    edit_daily_minutes = st.number_input(
        "1日の勤務時間（分）", min_value=1, max_value=1440, value=target.daily_work_minutes, step=15,
        key="edit_daily_minutes",
    )
    edit_max_consecutive = st.number_input(
        "最大連続勤務日数", min_value=1, value=target.max_consecutive_days, step=1,
        key="edit_max_consecutive",
    )
    edit_skill_level = st.selectbox(
        "スキル",
        options=list(range(SKILL_LEVEL_MIN, SKILL_LEVEL_MAX + 1)),
        index=target.skill_level - SKILL_LEVEL_MIN,
        format_func=format_skill_level,
        key="edit_skill_level",
    )

    st.markdown("**通常勤務可能曜日**")
    weekday_cols = st.columns(7)
    weekday_values: dict[int, bool] = {}
    for wd in range(7):
        with weekday_cols[wd]:
            weekday_values[wd] = st.checkbox(
                WEEKDAY_LABELS_JA[wd],
                value=target.weekday_availability.get(wd, False),
                key=f"weekday_{wd}",
            )

    edit_submitted = st.form_submit_button("更新")

    if edit_submitted:
        errors = validate_staff(
            edit_name, int(edit_daily_minutes), int(edit_max_consecutive), int(edit_skill_level)
        )
        if errors:
            for e in errors:
                st.error(e.message)
        else:
            repo.update_staff(
                conn,
                edit_target,
                staff_name=edit_name.strip(),
                role_id=edit_role_id,
                daily_work_minutes=int(edit_daily_minutes),
                max_consecutive_days=int(edit_max_consecutive),
                skill_level=int(edit_skill_level),
            )
            repo.save_weekday_availability(conn, edit_target, weekday_values)
            st.success("更新しました。")
            st.rerun()

st.markdown("**無効化**")
if target.active:
    confirm = st.checkbox("このスタッフを無効化することを確認しました", key="deactivate_confirm")
    if st.button("無効化する", disabled=not confirm):
        repo.deactivate_staff(conn, edit_target)
        st.success("無効化しました。")
        st.rerun()
else:
    st.caption("このスタッフはすでに無効です。")
