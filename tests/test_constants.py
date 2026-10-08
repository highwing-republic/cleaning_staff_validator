from src import constants as c


def test_role_codes():
    assert c.ROLE_CODES == ("LEADER", "CHECKER", "CLEANER")


def test_skill_levels():
    assert (c.SKILL_LEVEL_MIN, c.SKILL_LEVEL_MAX, c.SKILL_LEVEL_DEFAULT) == (1, 5, 3)
    assert sorted(c.SKILL_LEVELS) == [1, 2, 3, 4, 5]


def test_shift_types():
    assert c.SHIFT_TYPES == ("TIME_RANGE", "OTHER_DUTY", "BLANK", "UNKNOWN")


def test_import_statuses():
    assert c.IMPORT_STATUSES == ("ACTIVE", "SUPERSEDED")


def test_generation_constants_removed():
    """自動シフト生成（OR-Tools）由来の定数を持ち込まないこと.

    PREFERENCE_PREFER_OFF はPhase 6の勤務希望で正式に扱うため対象から外した。
    PREFERENCE_TYPES は希望を種別の行で持つ旧モデルの名残で、現在は1日1行の
    真偽値列で表すため存在しない。PREFERENCE_PREFER_WORK（勤務したい）は未採用。
    """
    for name in (
        "PREFERENCE_TYPES",
        "PREFERENCE_PREFER_WORK",
        "SCHEDULE_STATUS",
        "SOURCE_TYPES",
        "SOLVER_STATUSES",
        "STAGES",
        "TOTAL_SOLVE_TIME_LIMIT_SECONDS",
    ):
        assert not hasattr(c, name), name


def test_preference_kinds():
    assert c.PREFERENCE_ABSOLUTE_OFF == "ABSOLUTE_OFF"
    assert c.PREFERENCE_PREFER_OFF == "PREFER_OFF"
    assert c.PREFERENCE_EARLY_LEAVE == "EARLY_LEAVE"
    assert c.PREFERENCE_LATE_START == "LATE_START"
    assert c.PREFERENCE_AVAILABLE_EXTRA == "AVAILABLE_EXTRA"
    assert set(c.PREFERENCE_LABELS) == {
        c.PREFERENCE_ABSOLUTE_OFF,
        c.PREFERENCE_PREFER_OFF,
        c.PREFERENCE_EARLY_LEAVE,
        c.PREFERENCE_LATE_START,
        c.PREFERENCE_AVAILABLE_EXTRA,
    }


def test_time_statuses():
    assert c.TIME_STATUSES == ("OK", "UNSET", "INVALID", "NOT_APPLICABLE")
