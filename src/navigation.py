"""アプリ名と画面名の定義（画面間の案内文で同じ呼び方を使うため）.

画面名をここに集約することで、「シフト生成」「勤務表調整」などの呼び方が
画面によってずれないようにする。左メニューの番号は業務の順番を示すもので、
案内文では番号を付けずに画面名だけを使う（番号が変わっても文が古くならない）。
"""

APP_TITLE = "清掃勤務表作成"
APP_SUBTITLE = (
    "スタッフの通常勤務条件・勤務希望・予約状況から清掃勤務表を作成します。"
)

# 画面名（案内文ではこの名前を「」で囲んで使う）
PAGE_STAFF = "スタッフ管理"
PAGE_PREFERENCES = "勤務希望"
PAGE_REQUIREMENTS = "予約・必要人数"
PAGE_GENERATE = "シフト生成"
PAGE_SCHEDULE = "勤務表調整"
PAGE_OUTPUT = "勤務表出力"
PAGE_ATTENDANCE_IMPORT = "勤怠CSV取込"
PAGE_ATTENDANCE_VALIDATION = "勤怠データ検証"

SUPPORT_SUFFIX = "（補助）"

# 各画面の見出し（番号は業務の順番。各ページはここから見出しを取る）
HEADING_STAFF = f"① {PAGE_STAFF}"
HEADING_PREFERENCES = f"② {PAGE_PREFERENCES}"
HEADING_REQUIREMENTS = f"③ {PAGE_REQUIREMENTS}"
HEADING_GENERATE = f"④ {PAGE_GENERATE}"
HEADING_SCHEDULE = f"⑤ {PAGE_SCHEDULE}"
HEADING_OUTPUT = f"⑥ {PAGE_OUTPUT}"
HEADING_ATTENDANCE_IMPORT = f"{PAGE_ATTENDANCE_IMPORT}{SUPPORT_SUFFIX}"
HEADING_ATTENDANCE_VALIDATION = f"{PAGE_ATTENDANCE_VALIDATION}{SUPPORT_SUFFIX}"

# 左メニュー: 若女将が使う順番に並べる（ファイル名の番号は変更しない）
MAIN_PAGES: list[tuple[str, str]] = [
    ("pages/01_staff.py", HEADING_STAFF),
    ("pages/05_preferences.py", HEADING_PREFERENCES),
    ("pages/03_requirements.py", HEADING_REQUIREMENTS),
    ("pages/06_generate.py", HEADING_GENERATE),
    ("pages/07_schedule.py", HEADING_SCHEDULE),
    ("pages/08_output.py", HEADING_OUTPUT),
]

# 勤怠CSVの取込・検証は旧機能で、今の勤務表作成には使わない（残すが補助扱い）
SUPPORT_PAGES: list[tuple[str, str]] = [
    ("pages/02_attendance_import.py", PAGE_ATTENDANCE_IMPORT),
    ("pages/04_validation.py", PAGE_ATTENDANCE_VALIDATION),
]

MENU_PAGES: list[tuple[str, str]] = MAIN_PAGES + SUPPORT_PAGES

MAIN_SECTION_LABEL = "勤務表を作る"
SUPPORT_SECTION_LABEL = "補助機能（勤怠CSV）"

# ホーム画面に出す作業の流れ
USAGE_STEPS: list[str] = [
    f"1. 「{PAGE_STAFF}」でスタッフと通常の勤務条件を確認する",
    f"2. 「{PAGE_PREFERENCES}」で紙に書かれた「普段と違う希望」を転記する",
    f"3. 「{PAGE_REQUIREMENTS}」で予約室数と必要人数を入力する",
    f"4. 「{PAGE_GENERATE}」で勤務案を作り、下書き保存する",
    f"5. 「{PAGE_SCHEDULE}」で手直しして、日ごとに確定する",
    f"6. 「{PAGE_OUTPUT}」でExcelを出力して印刷する",
]
