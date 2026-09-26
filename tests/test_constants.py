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
    for name in (
        "PREFERENCE_TYPES",
        "PREFERENCE_PREFER_OFF",
        "PREFERENCE_PREFER_WORK",
        "SCHEDULE_STATUS",
        "SOURCE_TYPES",
        "SOLVER_STATUSES",
        "STAGES",
        "TOTAL_SOLVE_TIME_LIMIT_SECONDS",
    ):
        assert not hasattr(c, name), name
