"""予約・必要人数入力画面（日別の必要清掃体制）.

ふだんは「かんたん入力」: 1日あたり 予約室数（任意）・必要人数・備考 だけを入れる。
役割の内訳は必要人数から自動で決める（リーダー1・チェッカー1・残りクリーナー）。
最大人数・スキル・役割の個別設定・要件未設定は「詳しく設定する」を開いたときだけ出す。

予約室数と必要人数は別々の入力値として保存する。
アプリは「予約10室だから5人必要」のような推定をせず、若女将の判断をそのまま保持する。

詳しい設定では「要件未設定」と「必要人数0」を区別するため、日ごとに明示的な設定スイッチを持つ。
設定OFFの日は daily_requirements / daily_role_requirements の行を削除する
（行がない = 要件未設定）。
"""

import pandas as pd
import streamlit as st

from src import repositories as repo
from src import services
from src.constants import SKILL_LEVEL_MAX, SKILL_LEVEL_MIN
from src.models import DailyRequirementInput, RoleRequirementInput
from src.jp_holidays import DAY_COLOR_HOLIDAY, day_color, holiday_name
from src.navigation import (
    HEADING_REQUIREMENTS,
    PAGE_GENERATE,
    PAGE_REQUIREMENTS,
)
from src.period_utils import format_date_short, period_dates
from src.requirement_defaults import (
    WEEKDAY_REQUIRED_TOTAL,
    WEEKEND_REQUIRED_TOTAL,
    default_role_counts,
    describe_default_roles,
    describe_role_counts,
    required_total_for,
)
from src.requirement_display import (
    REQUIREMENT_UNDEFINED_LABEL,
    count_defined,
    count_undefined,
    format_max_staff,
    format_reserved_rooms,
    format_required_staff,
    format_role_condition,
    format_skill_condition,
    total_reserved_rooms,
)
from src.requirements_import import (
    ImportFormatError,
    normalize_requirements,
    read_csv_bytes,
    read_excel_bytes,
)
from src.simple_requirements import SimpleEntry, build_simple_save, follows_total
from src.ui_common import open_connection, select_period, select_year_month, show_errors

st.set_page_config(page_title=PAGE_REQUIREMENTS, layout="wide")
st.title(HEADING_REQUIREMENTS)

conn = open_connection()

FLASH_KEY = "_requirement_flash"
if FLASH_KEY in st.session_state:
    st.success(st.session_state.pop(FLASH_KEY))

start_date, days = select_period("requirement_period")
dates = period_dates(start_date, days)

roles = repo.list_roles(conn)
views = services.get_period_requirements(conn, dates)
view_by_date = {v.work_date: v for v in views}

ADVANCED_KEY = "requirements_advanced"
SIMPLE_SAVE_LABEL = "保存する"
ADVANCED_SAVE_LABEL = "この期間の予約・必要人数を保存"


def _reset_inputs_on_period_change() -> None:
    """対象期間が変わったら前の入力内容を残さない（別期間の値を誤保存しないため）."""
    context = (start_date, days)
    if st.session_state.get("_requirement_context") == context:
        return
    st.session_state["_requirement_context"] = context
    for key in [k for k in st.session_state if k.startswith("req_")]:
        del st.session_state[key]


_reset_inputs_on_period_change()

advanced = st.toggle(
    "詳しく設定する（役割の内訳・最大人数・スキル）",
    key=ADVANCED_KEY,
    help="ふだんは閉じたままで大丈夫です。役割の内訳を日ごとに変えたいときなどに開きます。",
)


def _optional_int(value) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _day_inputs(work_date: str) -> tuple[DailyRequirementInput | None, list[RoleRequirementInput]]:
    """1日分の入力欄. 設定OFFなら (None, []) を返す（= 要件未設定）."""
    view = view_by_date[work_date]
    req = view.requirement
    prefix = f"req_{work_date}"
    initial_role_counts = default_role_counts(work_date, roles)  # 平日・土日で違う

    label_col, defined_col, rooms_col, staff_col, role_col, skill_col, note_col = st.columns(
        [1.2, 0.8, 1.0, 1.6, 2.2, 1.8, 1.6]
    )

    with label_col:
        st.markdown(f"**{format_date_short(work_date)}**")
    with defined_col:
        # まだ設定していない日も、初期値を入れた状態で「設定」をオンにしておく
        defined = st.checkbox(
            "設定", value=True, key=f"{prefix}_defined",
            help="外すとこの日は要件未設定になります（必要人数0とは別です）。",
        )
    with rooms_col:
        reserved_rooms = st.number_input(
            "予約室数",
            min_value=0,
            value=req.reserved_rooms if req else None,
            step=1,
            key=f"{prefix}_reserved",
            label_visibility="collapsed",
            placeholder="予約室数",
        )
    with staff_col:
        required_col, max_col = st.columns(2)
        with required_col:
            required_total_staff = st.number_input(
                "最低必要人数",
                min_value=0,
                value=req.required_total_staff if req else required_total_for(work_date),
                step=1,
                key=f"{prefix}_required",
                label_visibility="collapsed",
            )
        with max_col:
            max_total_staff = st.number_input(
                "最大人数",
                min_value=0,
                value=req.max_total_staff if req else None,
                step=1,
                key=f"{prefix}_max",
                label_visibility="collapsed",
                placeholder="最大",
            )
    with role_col:
        role_columns = st.columns(len(roles)) if roles else []
        role_counts: dict[int, int] = {}
        for role, column in zip(roles, role_columns):
            with column:
                role_counts[role["role_id"]] = int(
                    st.number_input(
                        role["role_name"],
                        min_value=0,
                        value=(
                            view.role_counts.get(role["role_id"], 0)
                            if view.is_defined
                            else initial_role_counts[role["role_id"]]
                        ),
                        step=1,
                        key=f"{prefix}_role_{role['role_id']}",
                        label_visibility="collapsed",
                        placeholder=role["role_name"],
                    )
                    or 0
                )
    with skill_col:
        level_col, count_col = st.columns(2)
        with level_col:
            required_skill_level = st.number_input(
                "必要スキルLv",
                min_value=SKILL_LEVEL_MIN,
                max_value=SKILL_LEVEL_MAX,
                value=req.required_skill_level if req else None,
                step=1,
                key=f"{prefix}_skill_level",
                label_visibility="collapsed",
                placeholder="Lv",
            )
        with count_col:
            required_skill_count = st.number_input(
                "必要スキル人数",
                min_value=0,
                value=req.required_skill_count if req else 0,
                step=1,
                key=f"{prefix}_skill_count",
                label_visibility="collapsed",
                placeholder="人数",
            )
    with note_col:
        note = st.text_input(
            "備考",
            value=(req.note if req else None) or "",
            key=f"{prefix}_note",
            label_visibility="collapsed",
            placeholder="備考",
        )

    if not defined:
        return None, []

    requirement = DailyRequirementInput(
        work_date=work_date,
        required_total_staff=int(required_total_staff or 0),
        max_total_staff=_optional_int(max_total_staff),
        occupancy_rate=req.occupancy_rate if req else None,
        note=note.strip() or None,
        required_skill_level=_optional_int(required_skill_level),
        required_skill_count=int(required_skill_count or 0),
        reserved_rooms=_optional_int(reserved_rooms),
    )
    role_reqs = [
        RoleRequirementInput(work_date, role_id, count)
        for role_id, count in role_counts.items()
    ]
    return requirement, role_reqs


def _date_label(work_date: str) -> str:
    """土曜は青、日曜・祝日は赤で日付を出す. 祝日は名前も添える."""
    label = format_date_short(work_date)
    color = day_color(work_date)
    text = f"**:{color}[{label}]**" if color else f"**{label}**"
    name = holiday_name(work_date)
    return f"{text}  \n:{DAY_COLOR_HOLIDAY}[{name}]" if name else text


def _breakdown_text(view) -> str:
    """かんたん入力の「役割の内訳」欄. 自動の日は「自動」、個別に設定した日はその内訳."""
    if follows_total(view, roles):
        text = "自動"
    else:
        text = f"個別に設定（{describe_role_counts(view.role_counts, roles)}）"
    req = view.requirement
    extras = []
    if req is not None and req.max_total_staff is not None:
        extras.append(f"最大{req.max_total_staff}名")
    if req is not None and req.required_skill_count and req.required_skill_level is not None:
        extras.append(f"Lv{req.required_skill_level}以上{req.required_skill_count}名")
    if extras:
        text += "・" + "・".join(extras)
    return text


SIMPLE_COLUMNS = [1.0, 1.1, 1.1, 2.2, 2.2]


def _simple_row(work_date: str) -> SimpleEntry:
    """かんたん入力の1日分（予約室数・必要人数・備考）."""
    view = view_by_date[work_date]
    req = view.requirement
    prefix = f"req_simple_{work_date}"
    date_col, rooms_col, staff_col, roles_col, note_col = st.columns(
        SIMPLE_COLUMNS, vertical_alignment="center"
    )
    date_col.markdown(_date_label(work_date))
    reserved_rooms = rooms_col.number_input(
        "予約室数",
        min_value=0,
        value=req.reserved_rooms if req else None,
        step=1,
        key=f"{prefix}_reserved",
        label_visibility="collapsed",
        placeholder="未入力",
    )
    required_total_staff = staff_col.number_input(
        "必要人数",
        min_value=0,
        value=req.required_total_staff if req else required_total_for(work_date),
        step=1,
        key=f"{prefix}_required",
        label_visibility="collapsed",
    )
    roles_col.caption(_breakdown_text(view))
    note = note_col.text_input(
        "備考",
        value=(req.note if req else None) or "",
        key=f"{prefix}_note",
        label_visibility="collapsed",
        placeholder="備考（任意）",
    )
    return SimpleEntry(
        required_total_staff=int(required_total_staff or 0),
        reserved_rooms=_optional_int(reserved_rooms),
        note=note,
    )


def _simple_section() -> None:
    """かんたん入力: 1日の必要人数だけ入れて保存する."""
    st.caption(
        "日ごとに「必要人数」（最低これだけ必要な清掃スタッフの人数）を入れて保存してください。"
        "役割の内訳は人数から自動で決まります（リーダー1名・チェッカー1名・残りをクリーナー。"
        "例: 7名→クリーナー5名、6名→クリーナー4名）。予約室数と備考は空欄のままでも大丈夫です。"
    )
    unsaved = count_undefined(views)
    if unsaved:
        st.info(
            f"まだ保存していない日が{unsaved}日あります。ふだんの人数"
            f"（平日{WEEKDAY_REQUIRED_TOTAL}名・土日{WEEKEND_REQUIRED_TOTAL}名）を入れてあるので、"
            "変わる日だけ直して「保存する」を押してください。"
        )

    with st.form("requirements_simple_form"):
        header = st.columns(SIMPLE_COLUMNS)
        header[0].markdown("**日付**")
        header[1].markdown("**予約室数**")
        header[2].markdown("**必要人数**")
        header[3].markdown("**役割の内訳**")
        header[4].markdown("**備考**")
        entries = {work_date: _simple_row(work_date) for work_date in dates}
        submitted = st.form_submit_button(SIMPLE_SAVE_LABEL, type="primary")

    if submitted:
        requirements, role_requirements = build_simple_save(views, entries, roles)
        errors = services.save_period_requirements(conn, dates, requirements, role_requirements)
        if errors:
            show_errors(errors)
        else:
            st.session_state[FLASH_KEY] = f"保存しました（{len(dates)}日分）。"
            st.rerun()

    with st.expander("保存されている内容を一覧で見る"):
        _overview_table()


def _overview_table() -> None:
    overview = pd.DataFrame(
        [
            {
                "日付": format_date_short(v.work_date),
                "設定": "✓" if v.is_defined else "-",
                "予約室": format_reserved_rooms(v.requirement.reserved_rooms)
                if v.is_defined
                else "-",
                "最低必要人数": format_required_staff(v.requirement),
                "最大人数": format_max_staff(v.requirement),
                **{
                    role["role_name"]: format_role_condition(v, role["role_id"])
                    for role in roles
                },
                "Skill": format_skill_condition(v.requirement),
                "備考": (v.requirement.note or "") if v.is_defined else "",
            }
            for v in views
        ]
    )
    st.dataframe(overview, width="stretch", hide_index=True)


def _advanced_section() -> None:
    """詳しい設定: 役割の内訳・最大人数・スキル・要件未設定まで日ごとに入れる."""
    st.caption(
        "予約室数と必要清掃人数は別々の入力です（予約室数から人数を自動計算しません）。"
        "必要人数＝清掃勤務者がこれ以上必要な人数（ちょうどその人数という意味ではありません）。"
        "「設定」を外した日は要件未設定になります（必要人数0とは別の状態です）。"
    )
    summary = st.columns(3)
    summary[0].metric("設定済み日数", f"{count_defined(views)} / {len(views)}")
    summary[1].metric("未設定日数", count_undefined(views))
    summary[2].metric("予約室数合計", f"{total_reserved_rooms(views)}室")
    st.caption("予約室数合計は、予約室数が未入力の日を含みません。")

    st.subheader("期間内の設定状況")
    _overview_table()

    st.subheader("入力")
    st.caption(
        f"まだ設定していない日には、ふだんの体制（{describe_default_roles(roles)}）を"
        "初期値として入れてあります。変わる日だけ直して保存してください。"
    )
    st.caption(
        "「設定」を外した日は他の入力値を保存しません（要件未設定として行を削除します）。"
        "予約室数・最大人数・必要スキルLvは空欄にできます（空欄＝未設定、0＝0室/0名）。"
        "ここで役割の内訳を変えた日は、かんたん入力で人数を変えても内訳はそのままです。"
    )

    with st.form("requirements_form"):
        header = st.columns([1.2, 0.8, 1.0, 1.6, 2.2, 1.8, 1.6])
        header[0].markdown("**日付**")
        header[1].markdown("**設定**")
        header[2].markdown("**予約室数**")
        header[3].markdown("**必要 / 最大人数**")
        header[4].markdown("**" + " / ".join(r["role_name"] for r in roles) + "**")
        header[5].markdown("**必要Skill Lv / 人数**")
        header[6].markdown("**備考**")

        entered_requirements: list[DailyRequirementInput] = []
        entered_roles: list[RoleRequirementInput] = []
        for work_date in dates:
            requirement, role_reqs = _day_inputs(work_date)
            if requirement is not None:
                entered_requirements.append(requirement)
                entered_roles.extend(role_reqs)

        submitted = st.form_submit_button(ADVANCED_SAVE_LABEL, type="primary")

    if submitted:
        errors = services.save_period_requirements(
            conn, dates, entered_requirements, entered_roles
        )
        if errors:
            show_errors(errors)
        else:
            st.session_state[FLASH_KEY] = (
                f"保存しました（{len(dates)}日のうち設定済み: {len(entered_requirements)}日、"
                f"{REQUIREMENT_UNDEFINED_LABEL}: {len(dates) - len(entered_requirements)}日）。"
            )
            st.rerun()


if advanced:
    _advanced_section()
else:
    _simple_section()

# ---------------------------------------------------------------------------
# CSV / Excel 取り込み（従来どおり月単位）
# ---------------------------------------------------------------------------

with st.expander("CSV / Excel から取り込む"):
    st.caption(
        "取り込みは従来どおり月単位です（予約室数の列には未対応）。"
        "列: 日付, 稼働率(任意), 必要人数（列名は「最低人数」でも可）, 最大人数(任意), "
        "ロール別必要人数(role_codeまたはロール名, 任意), "
        "必要スキルLv(任意), 必要スキル人数(任意), 備考(任意)。"
        "全件エラーがない場合のみ保存します。"
    )

    import_year_month = select_year_month("requirements_import_year_month")
    uploaded = st.file_uploader("ファイルを選択", type=["csv", "xlsx"], key="requirements_upload")

    if uploaded is not None:
        data = uploaded.getvalue()
        try:
            if uploaded.name.lower().endswith(".xlsx"):
                import_df = read_excel_bytes(data)
            else:
                import_df = read_csv_bytes(data)
        except ImportFormatError as exc:
            st.error(str(exc))
        else:
            imported_daily, imported_role, import_errors = normalize_requirements(
                import_df, import_year_month, roles
            )
            if import_errors:
                st.error(f"{len(import_errors)}件のエラーがあります。全件成功時のみ保存します。")
                for e in import_errors:
                    st.error(e.message)
            else:
                st.success(f"{len(imported_daily)}件を読み込みました。内容を確認して保存してください。")
                preview = pd.DataFrame(
                    [
                        {
                            "日付": r.work_date,
                            "稼働率": r.occupancy_rate,
                            "必要人数": r.required_total_staff,
                            "最大人数": r.max_total_staff,
                            "必要スキルLv": r.required_skill_level,
                            "必要スキル人数": r.required_skill_count,
                            "備考": r.note,
                        }
                        for r in imported_daily
                    ]
                )
                st.dataframe(preview, width="stretch", hide_index=True)
                if st.button("取り込み内容を保存", key="save_import"):
                    repo.save_daily_requirements(conn, imported_daily)
                    repo.save_role_requirements(conn, imported_role)
                    st.success("保存しました。")
                    st.rerun()

st.divider()
st.caption(
    f"予約室数と必要人数の入力が終わったら、「{PAGE_GENERATE}」で勤務案を作成してください。"
)
