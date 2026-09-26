"""デモデータ投入（T88〜T90）.

15名（LEADER 3 / CHECKER 3 / CLEANER 9）、31日分の必要人数、全3種の希望を作る。

    python -m src.demo_data [YYYY-MM]
"""

import sys

from src import repositories as repo
from src.constants import (
    PREFERENCE_PREFER_OFF,
    PREFERENCE_PREFER_WORK,
    PREFERENCE_UNAVAILABLE,
)
from src.database import get_connection, initialize_database
from src.models import (
    DailyRequirementInput,
    MonthlyConditionInput,
    PreferenceInput,
    RoleRequirementInput,
)
from src.month_utils import get_month_dates, weekday_index

DEMO_YEAR_MONTH = "2026-10"

LEADER, CHECKER, CLEANER = 1, 2, 3

# (名前, role_id, 1日勤務分, 最大連勤, 勤務不可曜日, 所定日数, 最低日数, 最大日数, スキル)
DEMO_STAFF = [
    ("佐藤", LEADER, 480, 5, (), 20, 16, 22, 5),
    ("鈴木", LEADER, 480, 5, (6,), 20, None, 22, 5),
    ("高橋", LEADER, 480, 5, (), 18, None, 21, 4),
    ("田中", CHECKER, 480, 5, (), 20, 16, 22, 4),
    ("伊藤", CHECKER, 360, 5, (5,), 18, None, 21, 4),
    ("渡辺", CHECKER, 480, 5, (), 19, None, 22, 3),
    ("山本", CLEANER, 480, 5, (), 20, None, 22, 4),
    ("中村", CLEANER, 360, 5, (), 18, None, 21, 3),
    ("小林", CLEANER, 300, 4, (2,), 14, None, 17, 2),
    ("加藤", CLEANER, 480, 5, (), 20, None, 22, 3),
    ("吉田", CLEANER, 360, 5, (6,), 16, None, 19, 3),
    ("山田", CLEANER, 300, 4, (), 14, None, 17, 2),
    ("佐々木", CLEANER, 480, 5, (), 20, None, 22, 4),
    ("山口", CLEANER, 360, 5, (0,), 16, None, 19, 2),
    ("松本", CLEANER, 300, 4, (), 12, None, 15, 1),
]


def seed_demo(conn, year_month: str = DEMO_YEAR_MONTH) -> list[int]:
    """デモデータを投入し、作成したstaff_idを返す. 既存スタッフには手を付けない."""
    dates = get_month_dates(year_month)
    staff_ids: list[int] = []

    # T88 スタッフ
    for index, (
        name, role_id, minutes, max_consec, off_weekdays, target, min_d, max_d, skill,
    ) in enumerate(DEMO_STAFF):
        staff_id = repo.create_staff(conn, name, role_id, minutes, max_consec, skill)
        repo.save_weekday_availability(
            conn, staff_id, {w: w not in off_weekdays for w in range(7)}
        )
        repo.save_monthly_condition(
            conn,
            MonthlyConditionInput(
                staff_id=staff_id,
                year_month=year_month,
                target_monthly_minutes=target * minutes,
                min_monthly_minutes=None if min_d is None else min_d * minutes,
                max_monthly_minutes=None if max_d is None else max_d * minutes,
                carryover_consecutive_days=index % 3,
            ),
        )
        staff_ids.append(staff_id)

    # T89 必要人数（31日分）: 平日7名、金土8名、日曜9名
    requirements = []
    role_requirements = []
    for work_date in dates:
        weekday = weekday_index(work_date)
        required = {4: 8, 5: 8, 6: 9}.get(weekday, 7)
        requirements.append(
            DailyRequirementInput(
                work_date=work_date,
                required_total_staff=required,
                max_total_staff=required + 2,
                occupancy_rate=round(60 + required * 3.5, 1),
            )
        )
        role_requirements.append(RoleRequirementInput(work_date, LEADER, 1))
        role_requirements.append(RoleRequirementInput(work_date, CHECKER, 1))
    repo.save_daily_requirements(conn, requirements)
    repo.save_role_requirements(conn, role_requirements)

    # T90 希望（全3種混在）。PREFER_WORK は勤務可能曜日のみ
    for index, staff_id in enumerate(staff_ids):
        off_weekdays = DEMO_STAFF[index][4]
        repo.save_preference(
            conn, PreferenceInput(staff_id, dates[(index * 2) % len(dates)], PREFERENCE_UNAVAILABLE)
        )
        repo.save_preference(
            conn, PreferenceInput(staff_id, dates[(index * 2 + 9) % len(dates)], PREFERENCE_PREFER_OFF)
        )
        work_date = dates[(index * 2 + 17) % len(dates)]
        if weekday_index(work_date) not in off_weekdays:
            repo.save_preference(conn, PreferenceInput(staff_id, work_date, PREFERENCE_PREFER_WORK))
    return staff_ids


def main(argv: list[str]) -> None:
    year_month = argv[1] if len(argv) > 1 else DEMO_YEAR_MONTH
    conn = get_connection()
    initialize_database(conn)
    staff_ids = seed_demo(conn, year_month)
    conn.close()
    print(f"デモデータを投入しました: {year_month} / スタッフ{len(staff_ids)}名")


if __name__ == "__main__":
    main(sys.argv)
