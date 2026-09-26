"""シフト確認・編集・確定・Excel出力画面（T75〜T84）."""

import pandas as pd
import streamlit as st

from src import repositories as repo
from src import services
from src.export_excel import export_schedule_excel
from src.constants import (
    PREFERENCE_PREFER_OFF,
    PREFERENCE_PREFER_WORK,
    PREFERENCE_UNAVAILABLE,
    STAGES,
    STAGE_LABELS,
)
from src.month_utils import get_month_dates, round_half_up_workdays, weekday_index
from src.ui_common import WEEKDAY_LABELS_JA, open_connection, select_year_month, show_errors

st.set_page_config(page_title="シフト確認・編集", layout="wide")
st.title("⑦〜⑫ シフト確認・編集・固定・再計算・確定・Excel出力")

conn = open_connection()

year_month = select_year_month()

schedule_month = repo.get_schedule_month(conn, year_month)
if schedule_month is None:
    st.info("この月のシフトはまだ生成されていません。「⑤⑥ 事前チェック・シフト生成」画面で生成してください。")
    st.stop()

is_confirmed = schedule_month.status == "CONFIRMED"
if is_confirmed:
    st.info("この月は確定済みです（編集不可）。再編集するにはページ下部の「確定」から確定を解除してください。")

# ---------------------------------------------------------------------------
# Solver結果
# ---------------------------------------------------------------------------

st.subheader("Solver結果")


def _fmt_objective(value):
    return "—" if value is None else value


_stage_values = {
    1: schedule_month.objective_target_deviation,
    2: schedule_month.objective_max_deviation,
    3: schedule_month.objective_max_overstaff,
    4: schedule_month.objective_prefer_off,
    5: schedule_month.objective_prefer_work,
}
_obj_cols = st.columns(len(STAGES) + 1)
for _col, _stage in zip(_obj_cols, STAGES):
    _col.metric(f"{_stage}. {STAGE_LABELS[_stage]}", _fmt_objective(_stage_values[_stage]))
_obj_cols[-1].metric("最低人数を超える出勤（合計）", _fmt_objective(schedule_month.objective_overstaff))

dates = get_month_dates(year_month)
staff_list = [s for s in repo.list_staff(conn, include_inactive=True) if s.active]
staff_by_id = {s.staff_id: s for s in staff_list}
roles = repo.list_roles(conn)
role_names = {r["role_id"]: r["role_name"] for r in roles}

assignments = repo.load_assignments(conn, year_month)
assignment_by_key = {(a.staff_id, a.work_date): a for a in assignments}

requirements = {r.work_date: r for r in repo.get_daily_requirements(conn, year_month)}
role_requirements = {
    (r.work_date, r.role_id): r.required_count for r in repo.get_role_requirements(conn, year_month)
}
conditions = {c.staff_id: c for c in repo.get_monthly_conditions(conn, year_month)}
preferences = repo.get_preferences(conn, year_month)


def _col_label(work_date: str) -> str:
    _, month, day = work_date.split("-")
    return f"{int(month)}/{int(day)}({WEEKDAY_LABELS_JA[weekday_index(work_date)]})"


# ---------------------------------------------------------------------------
# T75 月間表
# ---------------------------------------------------------------------------

st.subheader("月間表")

col_labels = [_col_label(d) for d in dates]
table_rows = []
manual_mask_rows = []
for s in staff_list:
    row = {"スタッフ": s.staff_name}
    mask_row = {"スタッフ": False}
    for d, label in zip(dates, col_labels):
        a = assignment_by_key.get((s.staff_id, d))
        if a is None:
            text = ""
            is_manual = False
        else:
            if a.is_working:
                text = "○🔒" if a.is_locked else "○"
            else:
                text = "休🔒" if a.is_locked else ""
            is_manual = a.source == "MANUAL"
        row[label] = text
        mask_row[label] = is_manual
    table_rows.append(row)
    manual_mask_rows.append(mask_row)

table_df = pd.DataFrame(table_rows).set_index("スタッフ")
manual_flags: dict[tuple[str, str], bool] = {}
for staff_row, mask_row in zip(table_rows, manual_mask_rows):
    name = staff_row["スタッフ"]
    for label in col_labels:
        manual_flags[(name, label)] = mask_row[label]


def _highlight_manual(data):
    style_matrix = [
        [
            "background-color: #FFF3CD" if manual_flags.get((row_name, col_name)) else ""
            for col_name in data.columns
        ]
        for row_name in data.index
    ]
    return pd.DataFrame(style_matrix, index=data.index, columns=data.columns)


styled = table_df.style.apply(_highlight_manual, axis=None)
st.dataframe(styled, width="stretch")
st.caption("○=出勤 / 空欄=休み / 🔒=固定 / 黄色背景=手動変更（未固定含む）")

# ---------------------------------------------------------------------------
# T76 日別サマリー
# ---------------------------------------------------------------------------

st.subheader("日別サマリー")

daily_rows = []
for d in dates:
    req = requirements.get(d)
    working_ids = {
        a.staff_id for a in assignments if a.work_date == d and a.is_working and a.staff_id in staff_by_id
    }
    row = {
        "日付": _col_label(d),
        "最低": req.required_total_staff if req else None,
        "最大": req.max_total_staff if req else None,
        "出勤": len(working_ids),
    }
    for role in roles:
        required_role = role_requirements.get((d, role["role_id"]))
        attended_role = sum(
            1 for sid in working_ids if staff_by_id[sid].role_id == role["role_id"]
        )
        row[f"{role['role_name']}(出勤/必要)"] = f"{attended_role}/{required_role if required_role is not None else 0}"
    daily_rows.append(row)

st.dataframe(pd.DataFrame(daily_rows), width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# T77 スタッフ集計
# ---------------------------------------------------------------------------

st.subheader("スタッフ集計")

staff_rows = []
for s in staff_list:
    working_dates = {
        a.work_date for a in assignments if a.staff_id == s.staff_id and a.is_working
    }
    workday_count = len(working_dates)
    minutes = workday_count * s.daily_work_minutes
    cond = conditions.get(s.staff_id)
    if cond is not None:
        target_days = round_half_up_workdays(cond.target_monthly_minutes, s.daily_work_minutes)
        diff = workday_count - target_days
    else:
        target_days = None
        diff = None
    staff_rows.append(
        {
            "スタッフ": s.staff_name,
            "出勤日数": workday_count,
            "勤務時間(分)": minutes,
            "目標日数": target_days,
            "差": diff,
            "最低(分)": cond.min_monthly_minutes if cond else None,
            "最大(分)": cond.max_monthly_minutes if cond else None,
        }
    )

st.dataframe(pd.DataFrame(staff_rows), width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# T78 希望反映
# ---------------------------------------------------------------------------

st.subheader("希望反映")

working_by_staff_date = {
    (a.staff_id, a.work_date) for a in assignments if a.is_working
}


def _pref_stats(pref_type: str, respected_if_off: bool) -> tuple[int, float | None]:
    items = [p for p in preferences if p.preference_type == pref_type]
    if not items:
        return 0, None
    respected = 0
    for p in items:
        is_working = (p.staff_id, p.work_date) in working_by_staff_date
        ok = (not is_working) if respected_if_off else is_working
        if ok:
            respected += 1
    return len(items), respected / len(items) * 100


pref_rows = []
for label, code, respected_if_off in (
    ("絶対休み(UNAVAILABLE)", PREFERENCE_UNAVAILABLE, True),
    ("できれば休み(PREFER_OFF)", PREFERENCE_PREFER_OFF, True),
    ("できれば勤務(PREFER_WORK)", PREFERENCE_PREFER_WORK, False),
):
    count, rate = _pref_stats(code, respected_if_off)
    pref_rows.append(
        {
            "種別": label,
            "件数": count,
            "反映率": f"{rate:.0f}%" if rate is not None else "—",
        }
    )

st.dataframe(pd.DataFrame(pref_rows), width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# 検証結果
# ---------------------------------------------------------------------------

st.subheader("検証結果（Hard Constraint）")

validation_errors = services.validate_current_schedule(conn, year_month)
if validation_errors:
    st.error(f"{len(validation_errors)}件の違反があります。確定できません。")
    show_errors(validation_errors)
else:
    st.success("違反はありません。確定可能です。")

# ---------------------------------------------------------------------------
# T79〜T81 編集パネル
# ---------------------------------------------------------------------------

st.subheader("手動編集")

if is_confirmed:
    st.caption("確定済みのため編集できません。")
else:
    with st.form("manual_edit_form"):
        edit_staff_id = st.selectbox(
            "スタッフ",
            options=[s.staff_id for s in staff_list],
            format_func=lambda sid: staff_by_id[sid].staff_name,
        )
        edit_date = st.selectbox("日付", options=dates, format_func=_col_label)
        current = assignment_by_key.get((edit_staff_id, edit_date))
        default_working = current.is_working if current else False
        default_locked = current.is_locked if current else False

        work_choice = st.radio(
            "出勤 / 休み",
            options=["出勤", "休み"],
            index=0 if default_working else 1,
            horizontal=True,
        )
        lock_choice = st.checkbox("固定する", value=default_locked)

        edit_submitted = st.form_submit_button("この内容で更新")

        if edit_submitted:
            try:
                errors = services.apply_manual_edit(
                    conn,
                    edit_staff_id,
                    edit_date,
                    is_working=(work_choice == "出勤"),
                    is_locked=lock_choice,
                )
            except services.ScheduleConfirmedError as exc:
                st.error(str(exc))
            else:
                st.success("更新しました。")
                if errors:
                    st.warning(f"更新後のシフトに{len(errors)}件の違反があります。")
                    show_errors(errors)
                st.rerun()

# ---------------------------------------------------------------------------
# T82 再計算
# ---------------------------------------------------------------------------

st.subheader("再計算")
st.caption("固定していない手動変更は再計算で変更されます。")

if st.button("固定を残して再計算する", disabled=is_confirmed):
    try:
        outcome = services.generate_and_save(conn, year_month)
    except services.ScheduleConfirmedError as exc:
        st.error(str(exc))
    else:
        if not outcome.precheck.is_ok:
            st.error("事前チェックでエラーがあるため、再計算を中止しました。")
            show_errors(outcome.precheck.errors)
        elif outcome.result.status == "INFEASIBLE":
            st.error("現在の条件ではシフトを作成できません。既存のシフトは変更されていません。")
        elif outcome.result.status == "UNKNOWN":
            st.warning("時間内に解を見つけられませんでした。既存のシフトデータは変更していません。")
        else:
            st.success("再計算しました。")
            st.rerun()

# ---------------------------------------------------------------------------
# T83 確定
# ---------------------------------------------------------------------------

st.subheader("確定")

if is_confirmed:
    st.caption(f"確定日時: {schedule_month.confirmed_at}")
    with st.expander("確定を解除して再編集する"):
        st.warning(
            "確定を解除すると下書きに戻り、編集・固定・再計算ができるようになります。"
            "勤務内容と固定はそのまま残ります。"
            "確定時にダウンロードしたExcelは、解除前の確定記録として保管してください。"
        )
        agreed = st.checkbox("確定を解除することを確認しました", key="unconfirm_agree")
        if st.button("確定を解除する", disabled=not agreed):
            try:
                services.unconfirm_month(conn, year_month)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.success("確定を解除しました。再編集できます。")
                st.rerun()
else:
    if st.button("この月のシフトを確定する", type="primary"):
        try:
            outcome = services.confirm_month(conn, year_month)
        except services.ScheduleConfirmedError as exc:
            st.error(str(exc))
        else:
            if outcome.confirmed:
                st.success("確定しました。")
                st.rerun()
            else:
                st.error("違反があるため確定できません。")
                show_errors(outcome.errors)

# ---------------------------------------------------------------------------
# T85 Excel出力
# ---------------------------------------------------------------------------

st.subheader("Excel出力")

st.download_button(
    "Excelダウンロード",
    data=export_schedule_excel(
        services.load_scheduler_input(conn, year_month), assignments, role_names, schedule_month
    ),
    file_name=f"shift_{year_month}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
