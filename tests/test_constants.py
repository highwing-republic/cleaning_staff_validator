from src import constants as c


def test_role_codes():
    assert c.ROLE_CODES == ("LEADER", "CHECKER", "CLEANER")


def test_preference_types():
    assert c.PREFERENCE_TYPES == ("UNAVAILABLE", "PREFER_OFF", "PREFER_WORK")


def test_schedule_status():
    assert c.SCHEDULE_STATUS == ("DRAFT", "CONFIRMED")


def test_source_types():
    assert c.SOURCE_TYPES == ("OPTIMIZED", "MANUAL")


def test_solver_settings():
    assert c.TOTAL_SOLVE_TIME_LIMIT_SECONDS == 10.0
    assert isinstance(c.TOTAL_SOLVE_TIME_LIMIT_SECONDS, float)
    assert c.SOLVER_RANDOM_SEED == 42
    assert c.SOLVER_NUM_WORKERS == 1
    assert c.SOLVER_CP_MODEL_PRESOLVE is False


def test_solver_statuses():
    assert c.SOLVER_STATUSES == ("OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN")
