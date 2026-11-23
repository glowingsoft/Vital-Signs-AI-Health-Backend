# Vitals motor STUB: compares each reading against simple fixed limits.
# Each reading gets its own risk level; the most serious one becomes the motor's answer.
<<<<<<< Updated upstream

=======
# The routed AI model (app/motors/vitals/model.py) only writes the explanation, never the risk level;
# when it fails, the fixed explanation is used and the rule-based answer is still returned.
# HRV and sleep are only saved (no predictive model in this phase): never checked here, never sent to the AI.
# The model also picks a source id from the trusted library; the backend checks it (app/medical_sources/library.py).

import logging
from typing import Optional

from app.motors.runner import AllModelsFailed, add_failed_usage, parse_explanation, stub_result, usage_fields
from app.motors.vitals.model import MOTOR, READINGS, run_model
>>>>>>> Stashed changes
from app.shared.contracts import DISCLAIMER_ID, MotorOutput, highest_risk
from app.medical_sources.library import check_source


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


<<<<<<< Updated upstream
def check_blood_pressure(systolic: int, diastolic: int) -> str:
    if systolic >= 180 or diastolic >= 120:
=======
# Low and high blood pressure limits are final (agreed with the client).
def check_systolic(value: int) -> str:
    if value >= 180 or value < 70:
>>>>>>> Stashed changes
        return "critical"
    if systolic >= 140 or diastolic >= 90:
        return "high"
    if systolic >= 130 or diastolic >= 80:
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


# FDA wording rules (app/orchestrator/fda_filter.py): no certainty, no specific medical action,
# and every text points to a healthcare professional. The app shows the SOS and Call 911 buttons itself.
EXPLANATIONS = {
    "critical": "One or more of your readings appear far outside typical ranges. Please contact a healthcare professional right away.",
    "high": "One or more of your readings appear well outside typical ranges. Please contact a healthcare professional soon.",
    "medium": "One or more of your readings appear slightly outside typical ranges. It may help to review them with a healthcare professional.",
    "low": "Your readings appear within typical adult ranges. If anything worries you, talk to a healthcare professional.",
}

<<<<<<< Updated upstream

def analyze(data: dict) -> MotorOutput:
    # Only check the readings that were actually sent; any of them can be missing.
    levels = ["low"]
    if data.get("heart_rate") is not None:
        levels.append(check_heart_rate(data["heart_rate"]))
    if data.get("spo2") is not None:
        levels.append(check_spo2(data["spo2"]))
    if data.get("systolic") is not None and data.get("diastolic") is not None:
        levels.append(check_blood_pressure(data["systolic"], data["diastolic"]))
    if data.get("respiratory_rate") is not None:
        levels.append(check_respiratory_rate(data["respiratory_rate"]))

    risk_level = highest_risk(levels)
    return MotorOutput(
        risk_level=risk_level,
        confidence_score=0.8,  # fixed fake value; simple threshold rules are fairly reliable
        explanation=EXPLANATIONS[risk_level],
        disclaimer_id=DISCLAIMER_ID,
        finding_type="vital_thresholds",
        evidence_level="stub",
        model_name="stub",  # no AI model yet; token and cost fields keep their default of 0
=======
DATA_SAVED_EXPLANATION = (
    "Your HRV and sleep data were saved. There were no vital-sign readings to check. "
    "If anything worries you, talk to a healthcare professional."
)

# Reading -> its check, in the order the readings are listed in model.READINGS.
CHECKS = {
    "heart_rate": check_heart_rate,
    "spo2": check_spo2,
    "systolic": check_systolic,
    "diastolic": check_diastolic,
    "respiratory_rate": check_respiratory_rate,
}


def analyze(data: dict, country: Optional[str] = None) -> MotorOutput:
    """`country` = the patient's country (US or MX): which trusted medical sources the AI may cite."""
    if all(data.get(name) is None for name in READINGS) and (
        data.get("hrv_ms") is not None or data.get("sleep_minutes") is not None
    ):
        # Only HRV and/or sleep: nothing to check, and no AI call (no cost).
        return MotorOutput(
            risk_level="low",
            confidence_score=0.8,
            explanation=DATA_SAVED_EXPLANATION,
            disclaimer_id=DISCLAIMER_ID,
            finding_type="data_saved",  # nothing was checked, so no medical source
        )

    # Only check the readings that were actually sent; any of them can be missing.
    # Each blood pressure number is checked on its own, so one number alone is enough.
    levels = {name: check(data[name]) for name, check in CHECKS.items() if data.get(name) is not None}

    risk_level = highest_risk(["low", *levels.values()])  # decided before the AI call, which only explains it
    try:
        result = run_model(data, risk_level, country=country)
        answer = parse_explanation(result.text)
    except AllModelsFailed as error:
        logger.warning("Vitals: no AI explanation, using the fixed one: %s", error)
        result = add_failed_usage(stub_result(), error)  # still record any cost spent
        answer = parse_explanation("")
    return MotorOutput(
        risk_level=risk_level,
        confidence_score=0.8,  # fixed value; simple threshold rules are fairly reliable
        explanation=answer["explanation"] or EXPLANATIONS[risk_level],  # stub or no answer: the fixed text
        disclaimer_id=DISCLAIMER_ID,
        finding_type=answer["finding_type"] or "vital_thresholds",
        # The check after the AI answer: the source id must be in the trusted list for this country and motor.
        # Matched: the library's citation and link are attached. Otherwise: "pending_medical_review", no reference.
        **check_source(answer["source_id"], MOTOR, country),
        **usage_fields(result),  # model name, tokens and cost; "stub" and 0 when USE_BEDROCK is off
>>>>>>> Stashed changes
    )
