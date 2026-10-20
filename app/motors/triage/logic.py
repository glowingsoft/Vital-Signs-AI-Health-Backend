# Triage motor: keyword rules plus the routed AI model (app/motors/triage/model.py).
# With USE_BEDROCK off, only the keyword rules answer (the stub).

from app.motors.runner import parse_answer, usage_fields
from app.motors.triage.model import run_model
from app.shared.contracts import DISCLAIMER_ID, MotorOutput, highest_risk

# Checked from most to least serious; the first match wins.
CRITICAL_WORDS = ["chest pain", "can't breathe", "cannot breathe", "unconscious", "seizure"]
HIGH_WORDS = ["dizziness", "dizzy", "fainting", "severe", "vomiting blood"]
MEDIUM_WORDS = ["headache", "fever", "cough", "nausea", "pain"]


def pick_risk_level(text: str) -> str:
    text = text.lower()
    if any(word in text for word in CRITICAL_WORDS):
        return "critical"
    if any(word in text for word in HIGH_WORDS):
        return "high"
    if any(word in text for word in MEDIUM_WORDS):
        return "medium"
    return "low"


EXPLANATIONS = {
    "critical": "Your symptoms may need urgent attention. Please contact emergency services or a doctor now.",
    "high": "Your symptoms are worth reviewing with a healthcare professional soon.",
    "medium": "Your symptoms are worth keeping an eye on. Consider talking to a healthcare professional.",
    "low": "Your symptoms look mild. Keep monitoring how you feel.",
}


def analyze(data: dict) -> MotorOutput:
    rule_level = pick_risk_level(data.get("message", ""))
    # A red-flag keyword sends the text to the escalation model.
    result = run_model(data, escalate=rule_level == "critical")
    answer = parse_answer(result.text)  # None for the stub or an answer that breaks the format

    if answer is None:
        risk_level, confidence, explanation, finding_type = rule_level, 0.6, EXPLANATIONS[rule_level], "symptom_keywords"
    else:
        # The keyword rules stay a floor until the model is validated in M2: the model can raise the level, never lower it.
        risk_level = highest_risk([rule_level, answer["risk_level"]])
        confidence, explanation, finding_type = answer["confidence_score"], answer["explanation"], "ai_triage"

    return MotorOutput(
        risk_level=risk_level,
        confidence_score=confidence,
        explanation=explanation,
        disclaimer_id=DISCLAIMER_ID,
        finding_type=finding_type,
        evidence_level="stub",
        **usage_fields(result),  # model name, tokens and cost; "stub" and 0 when USE_BEDROCK is off
    )
