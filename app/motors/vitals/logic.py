# Vitals motor STUB: compares each reading against simple fixed limits.
# Each reading gets its own risk level; the most serious one becomes the motor's answer.

from app.shared.contracts import DISCLAIMER_ID, MotorOutput, highest_risk


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
    )
