# Triage motor: keyword rules plus the routed AI model (app/motors/triage/model.py).
# With USE_BEDROCK off, only the keyword rules answer (the stub).

from typing import List, Optional

from app.medical_sources.library import check_source, rule_source_id
from app.motors.runner import parse_answer, usage_fields
from app.motors.triage.model import MOTOR, run_model
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


# Words in the symptom text -> topics of the trusted source library (app/medical_sources/constants.TOPICS).
# Only used to pick a source when no AI model answered; the AI picks its own source otherwise.
TOPIC_WORDS = {
    "chest_pain": ["chest pain", "chest pressure", "chest tightness", "heart attack", "dolor en el pecho"],
    "breathing_difficulty": ["can't breathe", "cannot breathe", "short of breath", "shortness of breath",
                             "breathless", "difficulty breathing", "wheez", "dificultad para respirar"],
    "stroke_symptoms": ["stroke", "face droop", "slurred", "numb", "weakness on one side", "can't speak"],
    "altered_mental_status": ["unconscious", "confus", "faint", "seizure", "passed out", "convulsi"],
    "heart_rate": ["palpitation", "heart racing", "racing heart", "heartbeat", "pulse"],
    "blood_pressure": ["blood pressure", "hypertension", "presión"],
    "sepsis_infection": ["fever", "chills", "infection", "fiebre"],
    "shock": ["vomiting blood", "cold and clammy", "clammy"],
    "diabetes_glucose": ["diabet", "blood sugar", "glucose"],
    "wounds_skin": ["wound", "ulcer", "rash", "herida"],
    "burns_trauma": ["burn", "injur", "quemadura"],
    "heart_failure": ["swollen legs", "swollen ankles", "leg swelling"],
}


def symptom_topics(text: str) -> List[str]:
    """Library topics the symptom text mentions, in TOPIC_WORDS order (most serious first)."""
    text = text.lower()
    return [topic for topic, words in TOPIC_WORDS.items() if any(word in text for word in words)]


EXPLANATIONS = {
    "critical": "Your symptoms may need urgent attention. Please contact emergency services or a doctor now.",
    "high": "Your symptoms are worth reviewing with a healthcare professional soon.",
    "medium": "Your symptoms are worth keeping an eye on. Consider talking to a healthcare professional.",
    "low": "Your symptoms look mild. Keep monitoring how you feel.",
}


def analyze(data: dict, country: Optional[str] = None) -> MotorOutput:
    """`country` = the patient's country (US or MX): which trusted medical sources may be cited."""
    message = data.get("message", "")
    rule_level = pick_risk_level(message)
    # A red-flag keyword sends the text to the escalation model.
    result = run_model(data, escalate=rule_level == "critical", country=country)
    answer = parse_answer(result.text)  # None for the stub or an answer that breaks the format

    if answer is None:
        risk_level, confidence, explanation, finding_type = rule_level, 0.6, EXPLANATIONS[rule_level], "symptom_keywords"
        # No AI pick: the source the keywords point to (still only an allowed one, see check_source).
        picked = rule_source_id(MOTOR, country, symptom_topics(message))
    else:
        # The keyword rules stay a floor until the model is validated in M2: the model can raise the level, never lower it.
        risk_level = highest_risk([rule_level, answer["risk_level"]])
        confidence, explanation = answer["confidence_score"], answer["explanation"]
        finding_type, picked = answer["finding_type"] or "ai_triage", answer["source_id"]

    return MotorOutput(
        risk_level=risk_level,
        confidence_score=confidence,
        explanation=explanation,
        disclaimer_id=DISCLAIMER_ID,
        finding_type=finding_type,
        # The source check: the id must be an allowed source for this country and motor. Matched: the library's
        # evidence level, citation and link. Otherwise source_status "pending_medical_review" and no reference.
        **check_source(picked, MOTOR, country),
        **usage_fields(result),  # model name, tokens and cost; "stub" and 0 when USE_BEDROCK is off
    )
