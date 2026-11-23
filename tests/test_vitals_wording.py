# Tests for the vitals wording (FDA rules).
# Run with: pytest

import pytest

from app.motors.vitals import logic as vitals_logic
from app.orchestrator.fda_filter import SAFE_EXPLANATION, apply_fda_rules, contains_banned_phrase


@pytest.mark.parametrize("level", ["low", "medium", "high", "critical"])
def test_fixed_explanations_pass_the_fda_filter_and_point_to_a_professional(level):
    text = vitals_logic.EXPLANATIONS[level]
    assert not contains_banned_phrase(text)
    assert "healthcare professional" in text


def test_data_saved_explanation_points_to_a_professional():
    assert "healthcare professional" in vitals_logic.DATA_SAVED_EXPLANATION


@pytest.mark.parametrize("text", ["Please call 911 now.", "Go to the emergency room.", "Contact emergency services."])
def test_fda_filter_replaces_specific_emergency_actions(text):
    response = apply_fda_rules({"explanation": text})
    assert response["explanation"] == SAFE_EXPLANATION
