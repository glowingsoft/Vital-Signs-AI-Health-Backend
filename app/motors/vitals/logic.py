# Vitals motor: compares each reading against simple fixed limits.
# Each reading gets its own risk level; the most serious one becomes the motor's answer.
# The routed AI model (app/motors/vitals/model.py) only writes the explanation, never the risk level;
# when it fails, the fixed explanation is used and the rule-based answer is still returned.
# HRV and sleep are only saved (no predictive model in this phase): never checked here, never sent to the AI.

import logging

from app.motors.runner import AllModelsFailed, add_failed_usage, stub_result, usage_fields
from app.motors.vitals.model import READINGS, run_model
from app.shared.contracts import DISCLAIMER_ID, MotorOutput, highest_risk

logger = logging.getLogger(__name__)


def check_heart_rate(value: int) -> str:
    if value > 150 or value < 40:
        return "critical"
    if value > 120 or value < 50:
        return "high"
    if value > 100:
        return "medium"
    return "low"


def check_spo2(value: int) -> str:
    if value < 88:
        return "critical"
    if value < 92:
        return "high"
    if value < 95:
        return "medium"
    return "low"


# The low blood pressure limits are placeholders until the client's clinical supervisor approves them.
def check_systolic(value: int) -> str:
    if value >= 180 or value < 70:
        return "critical"
    if value >= 140 or value < 80:
        return "high"
    if value >= 130 or value < 90:
        return "medium"
    return "low"


def check_diastolic(value: int) -> str:
    if value >= 120 or value < 40:
        return "critical"
    if value >= 90 or value < 50:
        return "high"
    if value >= 80 or value < 60:
        return "medium"
    return "low"


def check_respiratory_rate(value: int) -> str:
    if value > 30 or value < 8:
        return "critical"
    if value > 24:
        return "high"
    if value > 20:
        return "medium"
    return "low"


EXPLANATIONS = {
    "critical": "One or more of your readings are far outside typical ranges. Please contact emergency services or a doctor now.",
    "high": "One or more of your readings are well outside typical ranges. Please check with a healthcare professional soon.",
    "medium": "One or more of your readings are slightly outside typical ranges. Keep monitoring them.",
    "low": "Your readings are within typical adult ranges.",
}

DATA_SAVED_EXPLANATION = "Your HRV and sleep data were saved. There were no vital-sign readings to check."


def analyze(data: dict) -> MotorOutput:
    if all(data.get(name) is None for name in READINGS) and (
        data.get("hrv_ms") is not None or data.get("sleep_minutes") is not None
    ):
        # Only HRV and/or sleep: nothing to check, and no AI call (no cost).
        return MotorOutput(
            risk_level="low",
            confidence_score=0.8,
            explanation=DATA_SAVED_EXPLANATION,
            disclaimer_id=DISCLAIMER_ID,
            finding_type="data_saved",
            evidence_level="stub",
        )

    # Only check the readings that were actually sent; any of them can be missing.
    levels = ["low"]
    if data.get("heart_rate") is not None:
        levels.append(check_heart_rate(data["heart_rate"]))
    if data.get("spo2") is not None:
        levels.append(check_spo2(data["spo2"]))
    # Each blood pressure number is checked on its own, so one number alone is enough.
    if data.get("systolic") is not None:
        levels.append(check_systolic(data["systolic"]))
    if data.get("diastolic") is not None:
        levels.append(check_diastolic(data["diastolic"]))
    if data.get("respiratory_rate") is not None:
        levels.append(check_respiratory_rate(data["respiratory_rate"]))

    risk_level = highest_risk(levels)  # decided before the AI call, which only explains it
    try:
        result = run_model(data, risk_level)
        explanation = result.text.strip() or EXPLANATIONS[risk_level]  # stub: "" -> the fixed text
    except AllModelsFailed as error:
        logger.warning("Vitals: no AI explanation, using the fixed one: %s", error)
        result = add_failed_usage(stub_result(), error)  # still record any cost spent
        explanation = EXPLANATIONS[risk_level]
    return MotorOutput(
        risk_level=risk_level,
        confidence_score=0.8,  # fixed value; simple threshold rules are fairly reliable
        explanation=explanation,
        disclaimer_id=DISCLAIMER_ID,
        finding_type="vital_thresholds",
        evidence_level="stub",
        **usage_fields(result),  # model name, tokens and cost; "stub" and 0 when USE_BEDROCK is off
    )
