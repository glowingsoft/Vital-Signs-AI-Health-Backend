# Tests for the low blood pressure limits of the vitals motor (app/motors/vitals/logic.py).
# The high limits are tested in tests/test_model_routing.py and are unchanged.
# Run with: pytest

import pytest

from app.motors.vitals import logic as vitals_logic


@pytest.mark.parametrize(
    "value,expected",
    [(65, "critical"), (69, "critical"), (70, "high"), (75, "high"), (80, "medium"), (85, "medium"),
     (90, "low"), (100, "low")],
)
def test_low_systolic(value, expected):
    assert vitals_logic.check_systolic(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [(35, "critical"), (40, "high"), (45, "high"), (50, "medium"), (55, "medium"), (60, "low"), (70, "low")],
)
def test_low_diastolic(value, expected):
    assert vitals_logic.check_diastolic(value) == expected


@pytest.mark.parametrize(
    "readings,expected",
    [
        ({"systolic": 85, "diastolic": 55}, "medium"),
        ({"systolic": 150, "diastolic": 55}, "high"),  # the most serious level wins
        ({"systolic": 65, "diastolic": 95}, "critical"),
        ({"systolic": 100, "diastolic": 65, "heart_rate": 160}, "critical"),
    ],
)
def test_low_blood_pressure_most_serious_level_wins(readings, expected):
    assert vitals_logic.analyze(readings).risk_level == expected
