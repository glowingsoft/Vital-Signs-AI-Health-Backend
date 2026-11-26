# Vitals motor: compares each reading against simple fixed limits.
# Each reading gets its own risk level; the most serious one becomes the motor's answer.
# The routed AI model (app/motors/vitals/model.py) only writes the explanation, never the risk level.

from typing import Optional

from app.medical_sources.library import check_source, rule_source_id
from app.motors.runner import parse_vitals_answer, usage_fields
from app.motors.vitals.model import MOTOR, run_model
from app.shared.contracts import DISCLAIMER_ID, RISK_ORDER, MotorOutput, highest_risk


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


def check_blood_pressure(systolic: int, diastolic: int) -> str:
    if systolic >= 180 or diastolic >= 120:
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


EXPLANATIONS = {
    "critical": "One or more of your readings are far outside typical ranges. Please contact emergency services or a doctor now.",
    "high": "One or more of your readings are well outside typical ranges. Please check with a healthcare professional soon.",
    "medium": "One or more of your readings are slightly outside typical ranges. Keep monitoring them.",
    "low": "Your readings are within typical adult ranges.",
}


def analyze(data: dict, country: Optional[str] = None) -> MotorOutput:
    """`country` = the patient's country (US or MX): which trusted medical sources may be cited."""
    # Only check the readings that were actually sent; any of them can be missing.
    # checked = (risk level, library topic) of each reading.
    checked = []
    if data.get("heart_rate") is not None:
        checked.append((check_heart_rate(data["heart_rate"]), "heart_rate"))
    if data.get("spo2") is not None:
        checked.append((check_spo2(data["spo2"]), "oxygen_saturation"))
    if data.get("systolic") is not None and data.get("diastolic") is not None:
        checked.append((check_blood_pressure(data["systolic"], data["diastolic"]), "blood_pressure"))
    if data.get("respiratory_rate") is not None:
        checked.append((check_respiratory_rate(data["respiratory_rate"]), "respiratory_rate"))

    risk_level = highest_risk(["low"] + [level for level, _ in checked])
    result = run_model(data, country=country)
    answer = parse_vitals_answer(result.text)  # None for the stub

    if answer is not None and answer["source_id"] is not None:
        explanation, finding_type, picked = answer["explanation"], answer["finding_type"], answer["source_id"]
    else:
        explanation = answer["explanation"] if answer else EXPLANATIONS[risk_level]  # stub: the fixed text
        finding_type = (answer or {}).get("finding_type")
        # No AI pick: the source for the most serious reading first (stable for equal levels).
        topics = [topic for _, topic in sorted(checked, key=lambda item: -RISK_ORDER.index(item[0]))]
        picked = rule_source_id(MOTOR, country, topics)
    return MotorOutput(
        risk_level=risk_level,
        confidence_score=0.8,  # fixed value; simple threshold rules are fairly reliable
        explanation=explanation,
        disclaimer_id=DISCLAIMER_ID,
        finding_type=finding_type or "vital_thresholds",
        # Matched: the library's evidence level, citation and link. Otherwise "pending_medical_review".
        **check_source(picked, MOTOR, country),
        **usage_fields(result),  # model name, tokens and cost; "stub" and 0 when USE_BEDROCK is off
    )
