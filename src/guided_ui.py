"""普段の作業を4段階にまとめた画面。業務判断はguided_workflowへ渡す。"""

from datetime import date

import pandas as pd
import streamlit as st

from src import guided_workflow as workflow, repositories as repo, services
from src.coverage_check import analyze_coverage, describe_problems, explain_shortage, problem_days
from src.monthly_hours import (
    WorkedShift,
    format_hours,
    format_month_total,
    month_column,
    monthly_totals,
    months_of,
    totals_by_month,
)
from src.daily_count_rows import (
    STAFF_COLUMN,
    build_schedule_table,
    counts_from_generation,
    counts_from_validation,
    table_styles,
)
from src.period_utils import default_period_start, format_date_short, format_period, period_dates
from src.preference_entry import render_preference_entry
from src.requirement_defaults import is_weekend_or_holiday
from src.navigation import PAGE_SCHEDULE
from src.schedule_excel import schedule_excel_filename
from src.schedule_output_display import (
    format_date_group,
    group_issues,
    is_distributable,
    not_distributable_message,
)
from src.schedule_print import build_schedule_print_html


def _flash(message):
    st.session_state["_guided_flash"] = message


def _clear_inputs():
    for key in list(st.session_state):
        if key.startswith("gw_"):
            del st.session_state[key]


def _select_period(start, days, step):
    _clear_inputs()
    st.session_state["_guided_period"] = (start, days)
    st.session_state["_shared_period"] = (start, days)
    st.session_state["_guided_step"] = step
    st.session_state.pop("_guided_preview", None)


def begin(mode):
    _clear_inputs()
    st.session_state.pop("_guided_error", None)
    st.session_state["_guided_mode"] = mode
    st.session_state.pop("_guided_period", None)
    st.session_state.pop("_guided_preview", None)


def _error(exc):
    st.session_state["_guided_error"] = str(exc)
    st.error(str(exc))
    if st.button("最新の内容を読み直す", key="gw_reload"):
        _clear_inputs()
        st.session_state.pop("_guided_preview", None)
        st.session_state.pop("_guided_error", None)
        st.rerun()


def render(conn):
    workflow.initialize(conn)
    if st.button("ホームへ戻る", key="guided_home"):
        _clear_inputs()
        st.session_state.pop("_guided_mode", None)
        st.session_state.pop("_guided_period", None)
        st.session_state.pop("_guided_preview", None)
        st.session_state.pop("_guided_error", None)
        st.rerun()
    if "_guided_error" in st.session_state:
        _error(st.session_state["_guided_error"])
        return
    if "_guided_flash" in st.session_state:
        st.success(st.session_state.pop("_guided_flash"))
    if "_guided_period" not in st.session_state:
        _choose_period(conn)
        return
    start, days = st.session_state["_guided_period"]
    dates = period_dates(start, days)
    step = st.session_state.get("_guided_step", workflow.get_progress(conn, start, days))
    st.subheader(format_period(start, days))
    st.progress(step / 4, text=f"{step} / 4　" + {1: "期間を選ぶ", 2: "休み・人数の確認", 3: "作成・手直し", 4: "確定・印刷"}[step])
    st.caption("保存した内容は、ブラウザを閉じても「作成途中の勤務表を開く」から再開できます。")
    try:
        if step == 2:
            _inputs(conn, start, days, dates)
        elif step == 3:
            _schedule(conn, start, days, dates)
        else:
            _finish(conn, start, days, dates)
    except workflow.WorkflowError as exc:
        _error(exc)


def _choose_period(conn):
    mode = st.session_state["_guided_mode"]
    if mode == "new":
        st.subheader("① いつからの勤務表を作りますか？")
        with st.form("gw_start"):
            start = st.date_input("開始日", value=date.fromisoformat(default_period_start()), format="YYYY/MM/DD")
            days = st.selectbox("何日分つくりますか？", [10, 11, 12, 13, 14], index=4,
                                format_func=lambda n: f"{n}日分")
            submitted = st.form_submit_button("この期間で始める", type="primary")
        if submitted:
            workflow.start_period(conn, start.isoformat(), days)
            _select_period(start.isoformat(), days, 2)
            st.rerun()
    else:
        complete = mode == "completed"
        st.subheader("完成した勤務表" if complete else "作成途中の勤務表")
        periods = [p for p in workflow.list_periods(conn) if p["complete"] == complete]
        if not periods:
            st.info("該当する勤務表はありません。ホームの「勤務表をつくる」から始めてください。")
            return
        index = st.selectbox("開く期間", range(len(periods)),
                            format_func=lambda i: format_period(periods[i]["start_date"], periods[i]["days"]))
        if st.button("この勤務表を開く", type="primary"):
            chosen = periods[index]
            workflow.start_period(conn, chosen["start_date"], chosen["days"])
            step = 4 if complete else workflow.get_progress(conn, chosen["start_date"], chosen["days"])
            _select_period(chosen["start_date"], chosen["days"], step)
            st.rerun()


def _inputs(conn, start, days, dates):
    st.subheader("② 休みの希望と必要人数を確認します")
    staff = repo.list_staff(conn, include_inactive=False)
    if not staff:
        st.info("初回だけスタッフの登録が必要です。通常の曜日・勤務時間を登録すると、次回から繰り返し使えます。")
        if st.button("スタッフ設定を開く"):
            st.switch_page("app_pages/01_staff.py")
        return

    st.markdown("**普段と違う希望だけ登録してください**")
    st.caption("紙に書かれた「普段と違う日」だけを登録します。登録しない日は、スタッフ設定の通常の曜日・時刻のままです。")
    render_preference_entry(
        conn,
        dates,
        repo.list_staff_details(conn, include_inactive=False),
        key="gw_pref",
        on_saved=_flash,
        empty_message="特別な希望は登録されていません。変更がなければ、下の必要人数を確認して進めます。",
    )
    _coverage_section(conn, dates)

    st.markdown("**最低何人必要ですか？**")
    st.caption(
        "この人数を満たすように配置します。多く入れるのは、週に働く日数の目安を決めている人の分だけです。"
        "予約室数から人数を自動計算することはありません。"
    )
    weekday, weekend = workflow.get_defaults(conn)
    views = services.get_period_requirements(conn, dates)
    with st.expander(f"基本設定：平日 {weekday}人・土日祝 {weekend}人"):
        with st.form("gw_defaults"):
            a, b = st.columns(2)
            weekday_new = a.number_input("平日の基本人数", min_value=0, value=weekday, step=1)
            weekend_new = b.number_input("土日祝の基本人数", min_value=0, value=weekend, step=1)
            st.caption("未入力の日の初期値として使います。すでに保存した日付の人数は変更しません。")
            save_default = st.form_submit_button("基本設定を保存")
        if save_default:
            workflow.save_defaults(conn, weekday_new, weekend_new)
            for view in views:
                if not view.is_defined:
                    st.session_state.pop(f"gw_count_{view.work_date}", None)
            _flash("基本設定を保存しました。各日の人数を確認してください。")
            st.rerun()
    base = st.session_state.setdefault("gw_counts_base", workflow.review_fingerprint(conn, dates, "requirements"))
    with st.form("gw_counts"):
        columns = st.columns(2)
        counts = {}
        for i, view in enumerate(views):
            initial = view.requirement.required_total_staff if view.is_defined else (weekend if is_weekend_or_holiday(view.work_date) else weekday)
            counts[view.work_date] = columns[i % 2].number_input(format_date_short(view.work_date) + " 最低人数", min_value=0, value=initial, step=1, key=f"gw_count_{view.work_date}")
        st.caption(
            "役割の内訳は人数から自動で決めます（リーダー1名・チェッカー1名・残りをクリーナー。例: 8名→クリーナー6名）。"
            "内訳を個別に変えた日や、保存済みの予約室数・最大人数・スキルの条件は維持されます。変更は左メニューの「予約・必要人数」から行えます。"
        )
        confirmed = st.checkbox("休みの希望を確認しました（特別な希望がなくてもチェックしてください）", key="gw_checked_preferences")
        submitted = st.form_submit_button("人数を保存して次へ", type="primary")
    if submitted:
        if not confirmed:
            st.warning("休みの希望を確認してからチェックしてください。")
        else:
            workflow.save_staff_counts(conn, dates, counts, base)
            workflow.confirm_inputs(conn, start, days)
            _clear_inputs()
            st.session_state["_guided_step"] = 3
            _flash("休みの希望と必要人数を確認・保存しました。")
            st.rerun()


def _coverage_section(conn, dates):
    """体制チェック: 出られる人が必要人数に届かない日（どう作っても足りない日）を、理由と一緒に知らせる."""
    days = problem_days(analyze_coverage(conn, dates))
    if not days:
        st.success("体制チェック：どの日も、出られる人が必要人数を満たしています。")
        return
    with st.container(border=True):
        st.markdown(f"**体制チェック：このままでは足りなくなる日（{len(days)}日）**")
        st.caption(
            "出られる人（普段の勤務曜日で「必ず休み」でない人）が、必要人数より少ない日です。"
            "勤務表をどう作っても足りません。休みの希望・必要人数・「普段休みの日に勤務できる」を見直してください。"
        )
        for day in days:
            st.markdown(f"- **{format_date_short(day.work_date)}**　" + "／".join(describe_problems(day)))
        if any(day.uses_defaults for day in days):
            st.caption("必要人数をまだ保存していない日は、初期値で判定しています。")


PERIOD_HOURS_COLUMN = "勤務時間"


def _month_column(month):
    return month_column(month)


def _add_hours(rows, staff_ids, dates, period_shifts, month_totals):
    """各行に「この期間の勤務時間」と「月ごとの合計（ほかの期間の保存済みも含む）」を足す."""
    period = totals_by_month(period_shifts)
    for row, staff_id in zip(rows, staff_ids):
        row[PERIOD_HOURS_COLUMN] = format_hours(
            sum(total.minutes for total in period.get(staff_id, {}).values())
        )
        for month in months_of(dates):
            row[_month_column(month)] = format_month_total(month_totals.get(staff_id, {}).get(month))
    return rows


def _show_schedule_table(rows, dates, counts):
    """勤務表を、先頭に日ごとの出勤人数・必要人数を付けて表示する（不足は赤、多すぎは黄色）.

    スタッフ名と「○月の合計」は左に固定し、日付の後ろに勤務日数・勤務時間を置く。
    """
    table = build_schedule_table(rows, dates, counts, format_date_short)
    month_columns = [_month_column(m) for m in months_of(dates) if _month_column(m) in table.columns]
    date_columns = [format_date_short(d) for d in dates]
    trailing = [c for c in table.columns if c not in {STAFF_COLUMN, *month_columns, *date_columns}]
    table = table[[STAFF_COLUMN, *month_columns, *date_columns, *trailing]].fillna("")
    styles = table_styles(table, dates, counts, format_date_short)
    st.caption("いちばん上の2行が、日ごとの出勤人数と必要人数です。足りない日は赤く表示します。表は横に動かして確認できます（名前の列は固定しています）。")
    if month_columns:
        st.caption(
            f"「{'」「'.join(month_columns)}」は、その月にすでに保存した勤務と、この勤務表の勤務を"
            "合わせた時間です（勤務表の開始〜終了の時間で数え、休憩は差し引いていません）。"
        )
    st.dataframe(
        table.style.apply(lambda _: styles, axis=None),
        hide_index=True,
        width="stretch",
        # 横に動かしても誰の行か・月の合計が分かるよう、スタッフ名と月の合計の列を固定する
        column_config={
            column: st.column_config.Column(pinned=True) for column in [STAFF_COLUMN, *month_columns]
        },
    )


def _saved_table(conn, dates):
    export = services.build_schedule_export(conn, dates)
    rows = [{"スタッフ": row.staff.staff_name,
             **{format_date_short(d): row.cells.get(d, "未作成") for d in dates},
             "勤務日数": row.working_days} for row in export.staff_rows]
    if rows:
        schedule = services.get_current_schedule(conn, dates)
        period_shifts = [
            WorkedShift(sid, view.work_date, a.start_time, a.end_time)
            for view in schedule.days if view.exists
            for sid, a in view.assignments.items() if a.is_working
        ]
        _add_hours(rows, [row.staff.staff_id for row in export.staff_rows], dates,
                   period_shifts, monthly_totals(conn, dates))
        _show_schedule_table(rows, dates, counts_from_validation(export.validation))
    return export


def _schedule(conn, start, days, dates):
    st.subheader("③ 勤務表をつくって確認します")
    current = services.get_current_schedule(conn, dates)
    if st.button("休み・人数の確認へ戻る"):
        _clear_inputs()
        st.session_state.pop("_guided_preview", None)
        st.session_state["_guided_step"] = 2
        st.rerun()
    complete = len(current.finalized_dates) == len(dates)
    if not complete:
        _coverage_section(conn, dates)
    label = "手直しを残して作り直す" if current.existing_dates else "勤務表をつくる"
    if st.button(label, type="primary", disabled=complete):
        with st.spinner("休みの希望と勤務日数を確認して作成しています…"):
            st.session_state["_guided_preview"] = workflow.make_preview(conn, dates)
    preview = st.session_state.get("_guided_preview")
    if preview:
        st.markdown("**新しい勤務案（まだ保存していません）**")
        result = preview.result
        rows = []
        for summary in result.staff_summaries:
            cells = {a.work_date: f"{a.start_time}–{a.end_time}" if a.is_working else "休" for a in result.assignments if a.staff_id == summary.staff_id}
            rows.append({"スタッフ": summary.staff_name, **{format_date_short(d): cells[d] for d in dates}, "勤務日数": summary.scheduled_days})
        maximums = {r.work_date: r.max_total_staff for r in repo.list_daily_requirements(conn, dates[0], dates[-1])}
        preview_shifts = [WorkedShift(a.staff_id, a.work_date, a.start_time, a.end_time)
                          for a in result.assignments if a.is_working]
        _add_hours(rows, [s.staff_id for s in result.staff_summaries], dates,
                   preview_shifts, monthly_totals(conn, dates, preview_shifts))
        _show_schedule_table(rows, dates, counts_from_generation(result, maximums))
        if result.shortage_days:
            coverage = {c.work_date: c for c in analyze_coverage(conn, dates)}
            role_names = {r["role_id"]: r["role_name"] for r in repo.list_roles(conn)}
            for day in result.shortage_days:
                st.warning(explain_shortage(day, coverage.get(day.work_date), role_names))
        changes = []
        for a in result.assignments:
            view = current.day(a.work_date)
            before = view.assignments.get(a.staff_id)
            if view.is_finalized or (before and before.locked):
                continue
            after = (a.is_working, a.start_time, a.end_time)
            if before is None or (before.is_working, before.start_time, before.end_time) != after:
                changes.append({"日付": format_date_short(a.work_date), "スタッフ": a.staff_name,
                    "変更前": "未作成" if before is None else f"{before.start_time}–{before.end_time}" if before.is_working else "休",
                    "変更後": f"{a.start_time}–{a.end_time}" if a.is_working else "休"})
        with st.expander(f"変更される箇所を確認（{len(changes)}件）"):
            st.dataframe(pd.DataFrame(changes), hide_index=True, width="stretch")
        left, right = st.columns(2)
        if left.button("この案を保存して使う", type="primary"):
            workflow.save_preview(conn, preview)
            st.session_state.pop("_guided_preview", None)
            _clear_inputs()
            _flash("勤務表を保存しました。必要な箇所を手直しできます。")
            st.rerun()
        if right.button("この案を取り消す"):
            st.session_state.pop("_guided_preview", None)
            st.rerun()
    if current.existing_dates:
        st.markdown("**保存してある勤務表**")
        _saved_table(conn, dates)
        _manual(conn, dates, current)
    if st.button("保存した勤務表で次へ", disabled=bool(current.missing_dates) or preview is not None):
        workflow.set_step(conn, start, days, 4)
        _clear_inputs()
        st.session_state["_guided_step"] = 4
        st.rerun()


def _manual(conn, dates, current):
    if not current.draft_dates:
        return
    with st.expander("出勤・休みを手直しする"):
        members = repo.list_staff(conn, include_inactive=True)
        ids = {sid for view in current.days for sid in view.assignments}
        names = {s.staff_id: s.staff_name + ("（無効）" if not s.active else "") for s in members if s.staff_id in ids}
        if not names:
            return
        sid = st.selectbox("手直しするスタッフ", list(names), format_func=names.get, key="gw_edit_staff")
        chosen = st.selectbox("手直しする日", current.draft_dates, format_func=format_date_short, key="gw_edit_date")
        existing = current.assignment(chosen, sid)
        if existing is None:
            st.info("このスタッフの行がまだありません。「手直しを残して作り直す」から作成してください。")
            return
        base_key = f"gw_manual_base_{sid}_{chosen}"
        base = st.session_state.setdefault(base_key, workflow.input_fingerprint(conn, dates))
        with st.form(f"gw_manual_{sid}_{chosen}"):
            state = st.radio("この日の勤務", ["出勤", "休み"], index=0 if existing.is_working else 1, horizontal=True)
            keep = st.checkbox("この勤務を自動作成で変えない", value=True)
            saved = st.form_submit_button("この変更を保存")
        if saved:
            workflow.save_manual_change(conn, dates, sid, chosen, state == "出勤", keep, base)
            st.session_state.pop("_guided_preview", None)
            _clear_inputs()
            _flash("手直しを保存しました。")
            st.rerun()


def _finish(conn, start, days, dates):
    st.subheader("④ 配布用に確定して印刷します")
    export = _saved_table(conn, dates)
    validation = export.validation
    if validation.finalized_days != len(dates):
        for group in group_issues(i for i in validation.issues if i.severity in ("ERROR", "WARNING")):
            st.warning(f"{format_date_group(group.work_dates)}：{group.message}")
        if st.button("手直しへ戻る"):
            _clear_inputs()
            st.session_state["_guided_step"] = 3
            st.rerun()
        base = st.session_state.setdefault("gw_finalize_base", workflow.input_fingerprint(conn, dates))
        with st.form("gw_finalize"):
            checked = st.checkbox("勤務表と不足などの注意事項を確認しました")
            st.caption("確定すると自動作成や手直しの対象から外れます。変更する場合は詳細操作の「勤務表調整」で日ごとに確定を解除できます。")
            submitted = st.form_submit_button("この期間を配布用に確定", type="primary", disabled=bool(validation.missing_days))
        if submitted:
            if not checked:
                st.warning("勤務表を確認してチェックしてください。")
            else:
                workflow.finalize_period(conn, dates, base, acknowledge_issues=True)
                _clear_inputs()
                _flash("配布用に確定しました。下のボタンから印刷できます。")
                st.rerun()
    else:
        st.success("この期間の勤務表は確定済みです。")
    render_outputs(conn, dates, export, key="gw_output",
                   how="先に上の「この期間を配布用に確定」で確定してください。")


EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DRAFT_OUTPUT_LABEL = "下書きとして印刷・保存する（確認用。配布しないでください）"
DRAFT_OUTPUT_CONFIRM = "配布しない下書きとして出力します"


def render_outputs(conn, dates, export, *, key, how, show_message=True):
    """印刷とExcel. 全日が確定していれば配布用に出す.

    確定していない日が1日でもあれば配布用は出さず、確認のための下書き出力だけを
    折りたたみの中に置く（チェックを入れたときだけ出す）。
    """
    validation = export.validation
    if not validation.existing_days:
        return
    if is_distributable(validation):
        render_print(export)
        _excel_button(conn, dates, "勤務表Excelをダウンロード", schedule_excel_filename(dates),
                      key=f"{key}_excel", primary=True)
        return
    if show_message:
        st.warning(not_distributable_message(validation, how))
    with st.expander(DRAFT_OUTPUT_LABEL):
        if st.checkbox(DRAFT_OUTPUT_CONFIRM, key=f"{key}_draft_ok"):
            render_print(export, draft=True)
            _excel_button(conn, dates, "下書きのExcelをダウンロード",
                          schedule_excel_filename(dates).replace(".xlsx", "_下書き.xlsx"),
                          key=f"{key}_draft_excel", primary=False)


def _excel_button(conn, dates, label, file_name, *, key, primary):
    try:
        data = services.export_schedule_excel(conn, dates)
    except Exception as exc:  # 出力に失敗しても画面は見られるようにする
        st.error(f"Excelの作成に失敗しました: {exc}")
        return
    st.download_button(label, data, file_name=file_name, mime=EXCEL_MIME,
                       type="primary" if primary else "secondary", key=key)


def render_print(export, draft=False):
    st.markdown("**下書きの印刷プレビュー（配布しないでください）**" if draft else "**印刷プレビュー**")
    st.caption("枠内の「印刷する」を押してください。")
    html = build_schedule_print_html(export)
    st.iframe(html, height=620)
    suffix = "_下書き" if draft else ""
    st.download_button("印刷用ファイルを保存", html, file_name=f"勤務表_{export.work_dates[0]}{suffix}.html",
                       mime="text/html")
    st.caption("印刷画面が開かない場合は、印刷用ファイルを保存してブラウザで開いてください。")
