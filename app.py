"""Streamlit エントリポイント.

左メニューは若女将が使う順番に並べる。勤怠CSVの取込・検証は旧機能なので
「補助機能」として分けて置く（削除はしない）。
ファイル名の番号は変えず、表示上の並びだけを業務順にしている。
"""

import streamlit as st

from src import services
from src.navigation import (
    APP_SUBTITLE,
    APP_TITLE,
    MAIN_PAGES,
    MAIN_SECTION_LABEL,
    MENU_PAGES,
    PAGE_GENERATE,
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
    navigation = st.navigation(
        {
            MAIN_SECTION_LABEL: [st.Page(home, title="ホーム", default=True)]
            + [st.Page(path, title=title) for path, title in MAIN_PAGES],
            SUPPORT_SECTION_LABEL: [
                st.Page(path, title=title) for path, title in SUPPORT_PAGES
            ],
        }
    )
    navigation.run()


def home() -> None:
    st.title(APP_TITLE)
    st.write(APP_SUBTITLE)
    st.caption("左のメニューから各画面へ移動してください。")

    st.subheader("勤務表作成の流れ")
    for step in USAGE_STEPS:
        st.write(step)

    _show_status()

    st.subheader("補助機能について")
    st.caption(
        "「勤怠CSV取込」「勤怠データ検証」は、勤怠システムから出した実績CSVを確認するための"
        "以前からの機能です。勤務表づくりには使いません。"
    )


def _show_status() -> None:
    """これから組む期間について、どこまで進んでいるかを示す."""
    start_date = default_period_start()
    dates = period_dates(start_date, PERIOD_DEFAULT_DAYS)
    status = services.get_home_status(open_connection(), dates)

    st.subheader("今の状況")
    st.caption(f"対象期間: {format_period(start_date, PERIOD_DEFAULT_DAYS)}")

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
