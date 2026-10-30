"""Streamlit エントリポイント.

左メニューは若女将が使う順番に並べる。勤怠CSVの取込・検証は旧機能なので
「補助機能」として分けて置く（削除はしない）。
ファイル名の番号は変えず、表示上の並びだけを業務順にしている。
"""

import streamlit as st

from src import services
from src import db_backup, guided_ui, guided_workflow
from src.navigation import (
    APP_SUBTITLE,
    APP_TITLE,
    HOME_PAGE_STATE_KEY,
    MAIN_PAGES,
    MAIN_SECTION_LABEL,
    MENU_PAGES,
    PAGE_GENERATE,
    PAGE_HOME,
    PAGE_OUTPUT,
    PAGE_REQUIREMENTS,
    PAGE_SCHEDULE,
    PAGE_STAFF,
    SUPPORT_PAGES,
    SUPPORT_SECTION_LABEL,
    USAGE_STEPS,
)
from src.period_utils import (
    PERIOD_DEFAULT_DAYS,
    default_period_start,
    format_period,
    period_dates,
)
from src.ui_common import open_connection

# MENU_PAGES / MAIN_PAGES / SUPPORT_PAGES は src/navigation.py の定義をそのまま再公開する
__all__ = [
    "APP_TITLE",
    "MAIN_PAGES",
    "MENU_PAGES",
    "SUPPORT_PAGES",
    "SUPPORT_SECTION_LABEL",
    "home",
    "main",
]


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    # Streamlit Cloud ではサーバーのファイルが再起動で消えるため、
    # どの画面よりも先に、非公開リポジトリの控えからデータを戻す（設定が無ければ何もしない）
    db_backup.ensure_restored()
    _show_backup_status()
    home_page = st.Page(home, title=PAGE_HOME, default=True)
    # スタッフ管理などから「ホーム・かんたん作成へ戻る」で戻れるようにする
    st.session_state[HOME_PAGE_STATE_KEY] = home_page
    navigation = st.navigation(
        {
            MAIN_SECTION_LABEL: [home_page],
            "スタッフ設定": [st.Page(MAIN_PAGES[0][0], title="スタッフ管理")],
            "詳細操作（必要なときだけ）": [st.Page(path, title=title) for path, title in MAIN_PAGES[1:]],
            SUPPORT_SECTION_LABEL: [
                st.Page(path, title=title) for path, title in SUPPORT_PAGES
            ],
        }
    )
    try:
        navigation.run()
    finally:
        # 保存ボタンのあとの再実行（st.rerun）でもここを通る。内容が変わっていれば控えを送る
        db_backup.backup_if_changed()


def _show_backup_status() -> None:
    """自動の控えの状態を知らせる（設定が無いPC版では何も出さない）."""
    state = db_backup.status()
    if state["error"]:
        (st.error if state["restore_failed"] else st.warning)(state["error"])
    if state["enabled"]:
        saved_at = state["last_saved_at"]
        st.sidebar.caption(
            "入力した内容は自動で保存先に控えています"
            + (f"（最終 {saved_at:%m/%d %H:%M}）" if saved_at else "")
        )


def home() -> None:
    st.title(APP_TITLE)
    conn = open_connection()
    try:
        guided_workflow.initialize(conn)
        if st.session_state.get("_guided_mode"):
            guided_ui.render(conn)
            return
        st.write("休みの希望を確認して、勤務表をつくりましょう。")
        st.caption("期間を選ぶ → 休み・人数の確認 → 作成・手直し → 確定・印刷")
        actions = (
            ("勤務表をつくる", "new", "次の期間の勤務表を、案内に沿って作成します。"),
            ("作成途中の勤務表を開く", "resume", "保存したところから再開します。"),
            ("完成した勤務表を見る・印刷する", "completed", "確定済みの勤務表を確認・印刷します。"),
        )
        for label, mode, help_text in actions:
            if st.button(label, key=f"home_{mode}", type="primary" if mode == "new" else "secondary", width="stretch"):
                guided_ui.begin(mode)
                st.rerun()
            st.caption(help_text)
        if not services.get_home_status(conn, period_dates(default_period_start(), PERIOD_DEFAULT_DAYS)).has_staff:
            st.info("スタッフがまだ登録されていません。初回だけ「スタッフ管理」で通常の曜日・勤務時間を登録してください。")
            if st.button("スタッフ設定を開く"):
                st.switch_page("pages/01_staff.py")
        with st.expander("登録状況・詳しい操作を確認する"):
            _show_status()
            st.caption(
                "以下は詳細操作の画面で作る場合の流れです。"
                "ふだんは上の「勤務表をつくる」から進めば、これらの画面を開く必要はありません。"
            )
            for step in USAGE_STEPS:
                st.write(step)
            st.caption("勤怠CSVの補助機能は、通常の勤務表づくりには使いません。")
    finally:
        conn.close()


def _show_status() -> None:
    """これから組む期間について、どこまで進んでいるかを示す."""
    start_date, days = st.session_state.get("_shared_period", (default_period_start(), PERIOD_DEFAULT_DAYS))
    dates = period_dates(start_date, days)
    conn = open_connection()
    try:
        status = services.get_home_status(conn, dates)
    finally:
        conn.close()

    st.subheader("今の状況")
    st.caption(f"対象期間: {format_period(start_date, days)}")

    cols = st.columns(5)
    cols[0].metric("登録スタッフ", f"{status.active_staff_count}名")
    cols[1].metric("勤務希望あり", f"{status.preference_days} / {status.period_days}日")
    cols[2].metric("必要人数の入力済み", f"{status.requirement_days} / {status.period_days}日")
    cols[3].metric("勤務表の作成済み", f"{status.schedule_days} / {status.period_days}日")
    cols[4].metric("確定済み", f"{status.finalized_days} / {status.period_days}日")

    if not status.has_staff:
        st.info(
            f"スタッフがまだ登録されていません。最初に「{PAGE_STAFF}」から登録してください。"
        )
        return
    if status.staff_without_work_time:
        st.warning(
            f"通常の勤務時間が未設定のスタッフが{status.staff_without_work_time}名います。"
            f"「{PAGE_STAFF}」で設定すると勤務案に入れられます。"
        )
    if status.requirement_days < status.period_days:
        st.info(
            f"必要人数が未入力の日が{status.period_days - status.requirement_days}日あります。"
            f"「{PAGE_REQUIREMENTS}」で入力してください。"
        )
    elif status.schedule_days == 0:
        st.info(f"必要人数の入力が終わっています。「{PAGE_GENERATE}」で勤務案を作成してください。")
    elif status.finalized_days < status.schedule_days:
        st.info(
            f"下書きの勤務表があります。「{PAGE_SCHEDULE}」で確認し、日ごとに確定してください。"
        )
    else:
        st.success(f"勤務表は確定済みです。「{PAGE_OUTPUT}」でExcelを出力できます。")


if __name__ == "__main__":
    main()
