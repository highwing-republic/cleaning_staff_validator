"""下書きを配布しないための出力の制御と、同じ注意のまとめ表示のテスト."""

from dataclasses import dataclass

from src.schedule_output_display import (
    format_date_group,
    group_issues,
)


@dataclass(frozen=True)
class Issue:
    work_date: str
    severity: str
    message: str
    code: str = "X"


DRAFT = "この日の勤務表は下書きです（「勤務表調整」で確定できます）。"


def test_format_date_group():
    assert format_date_group(["2026-11-01"]) == "11/1(日)"
    assert format_date_group([f"2026-11-{d:02d}" for d in range(1, 15)]) == "11/1(日)〜11/14(土)（14日）"
    assert format_date_group(["2026-11-05", "2026-11-03"]) == "11/3(火・祝)、11/5(木)（2日）"
    many = [f"2026-11-{d:02d}" for d in range(1, 16, 2)]
    assert format_date_group(many).endswith("ほか2日（8日）")


def test_same_notices_are_grouped_and_errors_come_first():
    issues = [Issue(f"2026-11-{d:02d}", "INFO", DRAFT) for d in range(1, 15)]
    issues += [Issue("2026-11-03", "ERROR", "1名不足しています。"), Issue("2026-11-05", "ERROR", "1名不足しています。")]
    groups = group_issues(issues)
    assert [(g.severity, len(g.work_dates)) for g in groups] == [("ERROR", 2), ("INFO", 14)]
    assert groups[1].message == DRAFT


def test_print_lists_a_draft_notice_once(tmp_path, monkeypatch):
    from src import services
    from src.database import get_connection, initialize_database
    from src.models import DailyRequirementInput
    from src import repositories as repo
    from src.period_utils import period_dates
    from src.schedule_print import build_schedule_print_html
    from src.ui_common import DB_PATH_ENV

    path = tmp_path / "out.sqlite"
    monkeypatch.setenv(DB_PATH_ENV, str(path))
    conn = get_connection(str(path))
    initialize_database(conn)
    repo.create_staff(conn, "1", "S1", 3, 3, standard_start_time="09:00", standard_end_time="15:30",
                      weekdays=list(range(7)))
    dates = period_dates("2026-11-02", 5)
    assert not services.save_period_requirements(conn, dates, [DailyRequirementInput(d, 1) for d in dates], [])
    services.save_generated_schedule(conn, dates, services.generate_schedule(conn, dates))
    html = build_schedule_print_html(services.build_schedule_export(conn, dates))
    assert html.count("下書きです") == 1
    assert "11/2(月)〜11/6(金)（5日）" in html
    conn.close()
