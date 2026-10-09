"""スタッフ管理画面.

スタッフマスターには「普段どう働く人か」（通常勤務条件）を登録する。
その日だけの早上がり・遅出はここでは扱わない（期間ごとの勤務希望で扱う）。
"""

import sqlite3

import pandas as pd
import streamlit as st

from src import repositories as repo
from src import services
from src.constants import (
    SKILL_LEVEL_DEFAULT,
    SKILL_LEVEL_MAX,
    SKILL_LEVEL_MIN,
    TARGET_DAYS_PER_WEEK_MAX,
    TARGET_DAYS_PER_WEEK_MIN,
    WEEKDAYS,
)
from src.navigation import (
    HEADING_STAFF,
    PAGE_PREFERENCES,
    PAGE_STAFF,
)
from src.ui_common import (
    WEEKDAY_LABELS_JA,
    format_optional_int,
    format_skill_level,
    format_weekdays,
    open_connection,
)
from src.work_time import format_standard_work_time

st.set_page_config(page_title=PAGE_STAFF, layout="wide")
st.title(HEADING_STAFF)
st.caption(
    "勤怠CSVとの照合には従業員番号を使用します（氏名では照合しません）。"
    "通常勤務曜日・通常勤務時間は「普段の働き方」です。"
    "その日だけの早上がり・遅出は勤務希望で登録します。"
)

conn = open_connection()

roles = repo.list_roles(conn)
role_ids = [r["role_id"] for r in roles]
role_name_by_id = {r["role_id"]: r["role_name"] for r in roles}
skill_options = list(range(SKILL_LEVEL_MIN, SKILL_LEVEL_MAX + 1))
special_skill_name_by_id = services.get_special_skill_names(conn)


def _optional_text(value: str) -> str | None:
    text = value.strip() if isinstance(value, str) else ""
    return text or None


def _optional_int(value) -> int | None:
    """number_input の値を任意整数にする（未入力はNone）."""
    if value is None or value == "":
        return None
    return int(value)


def _employee_code_taken(employee_code: str, exclude_staff_id: int | None = None) -> bool:
    existing = repo.get_staff_by_employee_code(conn, employee_code)
    return existing is not None and existing.staff_id != exclude_staff_id


def _weekday_checkboxes(key_prefix: str, selected: tuple[int, ...] = ()) -> list[int]:
    """通常勤務曜日のチェックボックス（月〜日）. 選択された曜日番号を返す."""
    st.markdown("**通常勤務曜日**")
    columns = st.columns(len(WEEKDAYS))
    chosen: list[int] = []
    for weekday, column in zip(WEEKDAYS, columns):
        with column:
            if st.checkbox(
                WEEKDAY_LABELS_JA[weekday],
                value=weekday in selected,
                key=f"{key_prefix}_weekday_{weekday}",
            ):
                chosen.append(weekday)
    return chosen


def _special_skill_multiselect(key: str, assigned: tuple[int, ...] = ()) -> list[int]:
    """特殊スキルの選択。選択肢はDBから取得する（コードに固定配列を持たない）."""
    options = services.list_special_skill_options(conn, assigned)
    if not options:
        st.caption("特殊スキルが登録されていません。")
        return []
    option_ids = [s.special_skill_id for s in options]
    name_by_id = {s.special_skill_id: s.skill_name for s in options}
    return st.multiselect(
        "特殊スキル",
        options=option_ids,
        default=[i for i in assigned if i in option_ids],
        format_func=lambda sid: name_by_id.get(sid, str(sid)),
        key=key,
    )


def _standard_time_inputs(key_prefix: str, start: str | None, end: str | None) -> tuple[str, str]:
    left, right = st.columns(2)
    with left:
        start_value = st.text_input(
            "通常開始時刻", value=start or "", placeholder="09:00", key=f"{key_prefix}_start"
        )
    with right:
        end_value = st.text_input(
            "通常終了時刻", value=end or "", placeholder="15:30", key=f"{key_prefix}_end"
        )
    preview = format_standard_work_time(start_value, end_value)
    st.caption(f"通常勤務時間: {preview}" if preview else "通常勤務時間: 未設定")
    return start_value, end_value


def _work_volume_inputs(
    key_prefix: str,
    target_days: int | None = None,
    max_days: int | None = None,
    max_consecutive: int | None = None,
) -> tuple[int | None, int | None, int | None]:
    """勤務量の任意項目。未入力（空欄）は「条件なし」として保存する."""
    left, center, right = st.columns(3)
    with left:
        target = st.number_input(
            "目標勤務日数 / 週（任意）",
            min_value=TARGET_DAYS_PER_WEEK_MIN,
            max_value=TARGET_DAYS_PER_WEEK_MAX,
            value=target_days,
            step=1,
            key=f"{key_prefix}_target_days",
            help="普段、週に何日程度勤務したいか。未入力なら目標勤務日数の条件なしとして扱います。",
        )
    with center:
        max_period = st.number_input(
            "期間内最大勤務日数（任意）",
            min_value=1,
            value=max_days,
            step=1,
            key=f"{key_prefix}_max_days",
            help=(
                "スタッフ個別の勤務量上限を将来設定するための欄です。"
                "期間の長さによって意味が変わるため、現時点では判定に使いません。"
            ),
        )
    with right:
        consecutive = st.number_input(
            "最大連続勤務日数（任意）",
            min_value=1,
            value=max_consecutive,
            step=1,
            key=f"{key_prefix}_max_consecutive",
            help="未入力なら個別の上限なしとして扱います。",
        )
    return _optional_int(target), _optional_int(max_period), _optional_int(consecutive)


# ---------------------------------------------------------------------------
# 一覧
# ---------------------------------------------------------------------------

st.subheader("一覧")
include_inactive = st.toggle("無効スタッフも表示する", value=True)
staff_details = repo.list_staff_details(conn, include_inactive=include_inactive)
staff_list = [d.staff for d in staff_details]

if staff_details:
    table = pd.DataFrame(
        [
            {
                "従業員番号": d.staff.employee_code,
                "氏名": d.staff.staff_name,
                "部門": d.staff.department or "",
                "ロール": role_name_by_id.get(d.staff.role_id, d.staff.role_id),
                "総合スキル": format_skill_level(d.staff.skill_level),
                "特殊スキル": " / ".join(
                    special_skill_name_by_id.get(i, str(i)) for i in d.special_skill_ids
                ),
                "通常勤務曜日": format_weekdays(d.weekdays),
                "通常勤務時間": format_standard_work_time(
                    d.staff.standard_start_time, d.staff.standard_end_time
                ),
                "目標勤務日数/週": format_optional_int(d.staff.target_days_per_week),
                "有効": "有効" if d.staff.active else "無効",
            }
            for d in staff_details
        ]
    )
    st.dataframe(table, width="stretch", hide_index=True)
else:
    st.info(
        "スタッフがまだ登録されていません。"
        "下の「新規スタッフ登録」から、通常の勤務曜日と勤務時間もあわせて登録してください。"
    )

# ---------------------------------------------------------------------------
# 新規作成
# ---------------------------------------------------------------------------

st.subheader("新規スタッフ登録")
with st.form("create_staff_form", clear_on_submit=True):
    # 既存テスト（AppTest）が text_input[0]/[1] と selectbox[0]/[1] を位置で参照するため、
    # 従業員番号・氏名・ロール・スキルはこの順のまま保つ
    employee_code = st.text_input("従業員番号")
    name = st.text_input("氏名")
    department = st.text_input("部門", value="清掃")
    role_id = st.selectbox(
        "ロール", options=role_ids,
        format_func=lambda rid: role_name_by_id.get(rid, str(rid)),
    )
    skill_level = st.selectbox(
        "総合スキル",
        options=skill_options,
        index=SKILL_LEVEL_DEFAULT - SKILL_LEVEL_MIN,
        format_func=format_skill_level,
    )
    new_special_skill_ids = _special_skill_multiselect("create_special_skills")
    new_weekdays = _weekday_checkboxes("create")
    new_start, new_end = _standard_time_inputs("create", None, None)
    new_target_days, new_max_days, new_max_consecutive = _work_volume_inputs("create")

    submitted = st.form_submit_button("登録")

    if submitted:
        errors = services.validate_staff_master_input(
            conn,
            employee_code,
            name,
            int(skill_level),
            standard_start_time=new_start,
            standard_end_time=new_end,
            target_days_per_week=new_target_days,
            max_days_per_period=new_max_days,
            max_consecutive_days=new_max_consecutive,
            weekdays=new_weekdays,
            special_skill_ids=new_special_skill_ids,
        )
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
                    standard_start_time=_optional_text(new_start),
                    standard_end_time=_optional_text(new_end),
                    target_days_per_week=new_target_days,
                    max_days_per_period=new_max_days,
                    max_consecutive_days=new_max_consecutive,
                    weekdays=new_weekdays,
                    special_skill_ids=new_special_skill_ids,
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
detail = repo.get_staff_detail(conn, edit_target)
target = detail.staff

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
        options=role_ids,
        index=role_ids.index(target.role_id),
        format_func=lambda rid: role_name_by_id.get(rid, str(rid)),
        key="edit_role_id",
    )
    edit_skill_level = st.selectbox(
        "総合スキル",
        options=skill_options,
        index=target.skill_level - SKILL_LEVEL_MIN,
        format_func=format_skill_level,
        key="edit_skill_level",
    )
    edit_special_skill_ids = _special_skill_multiselect(
        "edit_special_skills", detail.special_skill_ids
    )
    edit_weekdays = _weekday_checkboxes("edit", detail.weekdays)
    edit_start, edit_end = _standard_time_inputs(
        "edit", target.standard_start_time, target.standard_end_time
    )
    edit_target_days, edit_max_days, edit_max_consecutive = _work_volume_inputs(
        "edit",
        target.target_days_per_week,
        target.max_days_per_period,
        target.max_consecutive_days,
    )

    edit_submitted = st.form_submit_button("更新")

    if edit_submitted:
        errors = services.validate_staff_master_input(
            conn,
            edit_employee_code,
            edit_name,
            int(edit_skill_level),
            standard_start_time=edit_start,
            standard_end_time=edit_end,
            target_days_per_week=edit_target_days,
            max_days_per_period=edit_max_days,
            max_consecutive_days=edit_max_consecutive,
            weekdays=edit_weekdays,
            special_skill_ids=edit_special_skill_ids,
        )
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
                    standard_start_time=_optional_text(edit_start),
                    standard_end_time=_optional_text(edit_end),
                    target_days_per_week=edit_target_days,
                    max_days_per_period=edit_max_days,
                    max_consecutive_days=edit_max_consecutive,
                    weekdays=edit_weekdays,
                    special_skill_ids=edit_special_skill_ids,
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

st.divider()
st.caption(
    f"スタッフの登録・確認が終わったら、「{PAGE_PREFERENCES}」で"
    "紙に書かれた「普段と違う希望」を転記してください。"
)
