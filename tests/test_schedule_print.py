from src.constants import (
    SCHEDULE_DAY_DRAFT,
    SCHEDULE_DAY_FINALIZED,
    VALIDATION_STATUS_ERROR,
)
from src.models import (
    DailyRequirementInput,
    ScheduleDayValidationResult,
    ScheduleExport,
    ScheduleExportStaffRow,
    SchedulePeriodValidationResult,
    StaffInput,
    ValidationIssue,
)
from src.period_utils import period_dates
from src.schedule_print import build_schedule_print_html


def _export() -> ScheduleExport:
    dates = ["2026-10-26", "2026-10-27", "2026-10-28"]
    validation = SchedulePeriodValidationResult(
        work_dates=dates,
        days=[
            ScheduleDayValidationResult(
                work_date=dates[0],
                schedule_status=SCHEDULE_DAY_FINALIZED,
                requirement=DailyRequirementInput(work_date=dates[0], required_total_staff=2),
                scheduled_staff=2,
            ),
            ScheduleDayValidationResult(
                work_date=dates[1],
                schedule_status=SCHEDULE_DAY_DRAFT,
                requirement=DailyRequirementInput(work_date=dates[1], required_total_staff=3),
                scheduled_staff=2,
                schedule_issues=[
                    ValidationIssue(
                        code="STAFF_SHORTAGE",
                        severity=VALIDATION_STATUS_ERROR,
                        message="1名不足",
                        work_date=dates[1],
                    )
                ],
            ),
            ScheduleDayValidationResult(work_date=dates[2]),
        ],
    )
    return ScheduleExport(
        work_dates=dates,
        validation=validation,
        staff_rows=[
            ScheduleExportStaffRow(
                staff=StaffInput(staff_id=1, employee_code="001", staff_name="佐藤", role_id=1),
                role_name="清掃担当",
                cells={dates[0]: "09:00-15:00", dates[1]: "休"},
            )
        ],
        exported_at="2026-10-26T09:30:00",
    )


def test_build_schedule_print_html_includes_schedule_and_daily_summary() -> None:
    html = build_schedule_print_html(_export())

    assert "清掃スタッフ勤務表" in html
    assert "2026-10-26 ～ 2026-10-28" in html
    assert "2026-10-26T09:30:00" in html
    assert "佐藤" in html
    assert "清掃担当" in html
    assert '<span class="shift-time">09:00</span>' in html
    assert '<span class="shift-time">15:00</span>' in html
    assert "休" in html
    assert "未作成" in html
    assert "必要人数" in html
    assert "3" in html
    assert "当日変更・備考" in html
    assert "下書き" in html
    assert "確認が必要な日があります" in html
    assert "確認が必要な内容" in html
    assert "10/27(火)" in html
    assert "1名不足" in html


def test_build_schedule_print_html_escapes_user_controlled_content() -> None:
    export = _export()
    dangerous = ScheduleExportStaffRow(
        staff=StaffInput(staff_id=2, employee_code="002", staff_name='<img src=x onerror="alert(1)">', role_id=2),
        role_name="<script>alert('x')</script>",
        cells={"2026-10-26": '<svg onload="alert(1)">', "2026-10-27": "休"},
    )
    export = ScheduleExport(
        work_dates=export.work_dates,
        validation=SchedulePeriodValidationResult(
            work_dates=export.validation.work_dates,
            days=[
                export.validation.days[0],
                ScheduleDayValidationResult(
                    work_date=export.validation.work_dates[1],
                    schedule_status=SCHEDULE_DAY_DRAFT,
                    schedule_issues=[
                        ValidationIssue(
                            code="STAFF_SHORTAGE",
                            severity=VALIDATION_STATUS_ERROR,
                            message="<script>alert('issue')</script>",
                            work_date=export.validation.work_dates[1],
                        )
                    ],
                ),
                export.validation.days[2],
            ],
        ),
        staff_rows=[dangerous],
        exported_at='now"><script>alert(2)</script>',
    )

    html = build_schedule_print_html(export)

    assert '&lt;img src=x onerror=&quot;alert(1)&quot;&gt;' in html
    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in html
    assert '&lt;svg onload=&quot;alert(1)&quot;&gt;' in html
    assert 'now&quot;&gt;&lt;script&gt;alert(2)&lt;/script&gt;' in html
    assert "&lt;script&gt;alert(&#x27;issue&#x27;)&lt;/script&gt;" in html
    assert "<script>alert" not in html
    assert "<img src=x" not in html


def test_print_controls_and_multipage_table_rules_are_present() -> None:
    html = build_schedule_print_html(_export())

    assert "@page { size: A4 landscape" in html
    assert 'onclick="window.print()"' in html
    assert ".toolbar { display: none !important; }" in html
    assert "display: table-header-group" in html
    assert "break-inside: avoid" in html
    assert "page-break-inside: avoid" in html


def test_fourteen_days_and_forty_staff_fit_fixed_width_schedule_cells() -> None:
    dates = period_dates("2026-10-01", 14)
    rows = [
        ScheduleExportStaffRow(
            staff=StaffInput(staff_id=index, employee_code=f"{index:03}", staff_name=f"スタッフ{index:02}", role_id=1),
            role_name="清掃担当",
            cells={date: "09:00-15:00" for date in dates},
        )
        for index in range(1, 41)
    ]
    export = ScheduleExport(
        work_dates=dates,
        validation=SchedulePeriodValidationResult(
            work_dates=dates,
            days=[
                ScheduleDayValidationResult(work_date=date, schedule_status=SCHEDULE_DAY_FINALIZED)
                for date in dates
            ],
        ),
        staff_rows=rows,
        exported_at="2026-10-01T08:00:00",
    )

    html = build_schedule_print_html(export)

    assert html.count('class="staff-name"') == 40
    assert html.count('<span class="shift-time">09:00</span>') == 40 * 14
    assert html.count('<span class="shift-time">15:00</span>') == 40 * 14
    assert ".shift { white-space: normal; }" in html
    assert ".shift-time { display: block; white-space: nowrap; }" in html

