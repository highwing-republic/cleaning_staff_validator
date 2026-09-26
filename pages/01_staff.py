"""スタッフ管理画面."""

import sqlite3

import pandas as pd
import streamlit as st

from src import repositories as repo
from src.constants import SKILL_LEVEL_DEFAULT, SKILL_LEVEL_MAX, SKILL_LEVEL_MIN
from src.ui_common import format_skill_level, open_connection
from src.validation import validate_staff

st.set_page_config(page_title="スタッフ管理", layout="wide")
st.title("① スタッフ管理")
st.caption("勤怠CSVとの照合には従業員番号を使用します（氏名では照合しません）。")

conn = open_connection()

roles = repo.list_roles(conn)
role_name_by_id = {r["role_id"]: r["role_name"] for r in roles}
skill_options = list(range(SKILL_LEVEL_MIN, SKILL_LEVEL_MAX + 1))


def _optional_text(value: str) -> str | None:
    text = value.strip()
    return text or None


def _employee_code_taken(employee_code: str, exclude_staff_id: int | None = None) -> bool:
    existing = repo.get_staff_by_employee_code(conn, employee_code)
    return existing is not None and existing.staff_id != exclude_staff_id


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
                "従業員番号": s.employee_code,
                "氏名": s.staff_name,
                "部門": s.department or "",
                "ロール": role_name_by_id.get(s.role_id, s.role_id),
                "スキル": format_skill_level(s.skill_level),
                "有効": "有効" if s.active else "無効",
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
    employee_code = st.text_input("従業員番号")
    name = st.text_input("氏名")
    department = st.text_input("部門", value="清掃")
    role_id = st.selectbox(
        "ロール", options=[r["role_id"] for r in roles],
        format_func=lambda rid: role_name_by_id.get(rid, str(rid)),
    )
    skill_level = st.selectbox(
        "スキル",
        options=skill_options,
        index=SKILL_LEVEL_DEFAULT - SKILL_LEVEL_MIN,
        format_func=format_skill_level,
    )
    submitted = st.form_submit_button("登録")

    if submitted:
        errors = validate_staff(employee_code, name, int(skill_level))
        if not errors and _employee_code_taken(employee_code.strip()):
            st.error(f"従業員番号「{employee_code.strip()}」はすでに登録されています。")
        elif errors:
            for e in errors:
                st.error(e.message)
        else:
            try:
                new_id = repo.create_staff(
                    conn,
                    employee_code.strip(),
                    name.strip(),
                    role_id,
                    int(skill_level),
                    _optional_text(department),
                )
            except sqlite3.IntegrityError:
                st.error(f"従業員番号「{employee_code.strip()}」はすでに登録されています。")
            else:
                st.success(f"スタッフ「{name.strip()}」を登録しました（ID: {new_id}）。")
                st.rerun()

# ---------------------------------------------------------------------------
# 編集・無効化
# ---------------------------------------------------------------------------

st.subheader("編集")

if not staff_list:
    st.stop()

edit_target = st.selectbox(
    "編集するスタッフ",
    options=[s.staff_id for s in staff_list],
    format_func=lambda sid: next(
        f"{s.employee_code} {s.staff_name}" for s in staff_list if s.staff_id == sid
    ),
    key="edit_target_staff",
)
target = repo.get_staff(conn, edit_target)

with st.form("edit_staff_form"):
    edit_employee_code = st.text_input(
        "従業員番号", value=target.employee_code, key="edit_employee_code"
    )
    edit_name = st.text_input("氏名", value=target.staff_name, key="edit_name")
    edit_department = st.text_input(
        "部門", value=target.department or "", key="edit_department"
    )
    edit_role_id = st.selectbox(
        "ロール",
        options=[r["role_id"] for r in roles],
        index=[r["role_id"] for r in roles].index(target.role_id),
        format_func=lambda rid: role_name_by_id.get(rid, str(rid)),
        key="edit_role_id",
    )
    edit_skill_level = st.selectbox(
        "スキル",
        options=skill_options,
        index=target.skill_level - SKILL_LEVEL_MIN,
        format_func=format_skill_level,
        key="edit_skill_level",
    )

    edit_submitted = st.form_submit_button("更新")

    if edit_submitted:
        errors = validate_staff(edit_employee_code, edit_name, int(edit_skill_level))
        code = edit_employee_code.strip() if isinstance(edit_employee_code, str) else ""
        if not errors and _employee_code_taken(code, exclude_staff_id=edit_target):
            st.error(f"従業員番号「{code}」はすでに登録されています。")
        elif errors:
            for e in errors:
                st.error(e.message)
        else:
            try:
                repo.update_staff(
                    conn,
                    edit_target,
                    employee_code=code,
                    staff_name=edit_name.strip(),
                    role_id=edit_role_id,
                    skill_level=int(edit_skill_level),
                    department=_optional_text(edit_department),
                )
            except sqlite3.IntegrityError:
                st.error(f"従業員番号「{code}」はすでに登録されています。")
            else:
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
