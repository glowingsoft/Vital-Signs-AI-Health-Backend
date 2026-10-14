# Triage motor STUB: looks for keywords in the symptom text and picks a risk level.
# No AI yet; this only returns correctly shaped fake answers.

from app.shared.contracts import DISCLAIMER_ID, MotorOutput

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
    risk_level = pick_risk_level(data.get("message", ""))
    return MotorOutput(
        risk_level=risk_level,
        confidence_score=0.6,  # fixed fake value until a real model is plugged in
        explanation=EXPLANATIONS[risk_level],
        disclaimer_id=DISCLAIMER_ID,
        finding_type="symptom_keywords",
        evidence_level="stub",
        model_name="stub",  # no AI model yet; token and cost fields keep their default of 0
    )
