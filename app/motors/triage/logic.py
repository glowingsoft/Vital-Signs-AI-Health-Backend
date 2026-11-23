# Triage motor: keyword rules plus the routed AI model (app/motors/triage/model.py).
# With USE_BEDROCK off, only the keyword rules answer (the stub).

<<<<<<< HEAD
from app.motors.runner import parse_answer, usage_fields
from app.motors.triage.model import run_model
from app.shared.contracts import DISCLAIMER_ID, MotorOutput, highest_risk
=======
<<<<<<< Updated upstream
from app.shared.contracts import DISCLAIMER_ID, MotorOutput
>>>>>>> v2_main

# Checked from most to least serious; the first match wins.
CRITICAL_WORDS = ["chest pain", "can't breathe", "cannot breathe", "unconscious", "seizure"]
HIGH_WORDS = ["dizziness", "dizzy", "fainting", "severe", "vomiting blood"]
MEDIUM_WORDS = ["headache", "fever", "cough", "nausea", "pain"]
=======
import dataclasses
import logging
import time
from typing import Optional

from app import config
from app.configurations.routing import TRIAGE_ESCALATION_CONFIDENCE
from app.motors.runner import (
    AllModelsFailed, ModelResult, add_failed_usage, add_usage, parse_answer, stub_result, usage_fields,
)
from app.motors.triage.model import MOTOR, run_model
from app.shared.contracts import DISCLAIMER_ID, MotorOutput, highest_risk
from app.medical_sources.library import check_source

logger = logging.getLogger(__name__)

# Never "low": without an AI answer we cannot say the symptoms are mild.
UNAVAILABLE_EXPLANATION = "We could not complete the check right now. Please contact a healthcare professional."
>>>>>>> Stashed changes


def pick_risk_level(text: str) -> str:
    text = text.lower()
    if any(word in text for word in CRITICAL_WORDS):
        return "critical"
    if any(word in text for word in HIGH_WORDS):
        return "high"
    if any(word in text for word in MEDIUM_WORDS):
        return "medium"
    return "low"


<<<<<<< Updated upstream
EXPLANATIONS = {
    "critical": "Your symptoms may need urgent attention. Please contact emergency services or a doctor now.",
    "high": "Your symptoms are worth reviewing with a healthcare professional soon.",
    "medium": "Your symptoms are worth keeping an eye on. Consider talking to a healthcare professional.",
    "low": "Your symptoms look mild. Keep monitoring how you feel.",
}


def analyze(data: dict) -> MotorOutput:
<<<<<<< HEAD
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

=======
    risk_level = pick_risk_level(data.get("message", ""))
=======
def unavailable_output(result: ModelResult) -> MotorOutput:
    return MotorOutput(
        risk_level="medium",
        confidence_score=0.0,
        explanation=UNAVAILABLE_EXPLANATION,
        disclaimer_id=DISCLAIMER_ID,
        finding_type="ai_unavailable",  # nothing was assessed, so no medical source
        **usage_fields(result),
    )


def analyze(data: dict, country: Optional[str] = None) -> MotorOutput:
    """`country` = the patient's country (US or MX): which trusted medical sources the AI may cite."""
    # One time budget for every call: default model, retries, fallbacks and escalation.
    deadline = time.monotonic() + config.motor_budget_seconds("triage")
    try:
        result = run_model(data, deadline=deadline, country=country)
    except AllModelsFailed as error:
        logger.warning("Triage: no AI answer: %s", error)
        return unavailable_output(add_failed_usage(stub_result(), error))

    answer = parse_answer(result.text)  # None only for the stub: run_chain checks the JSON
    if answer is None:
        return unavailable_output(result)

    if needs_escalation(answer):
        first_answer = answer
        try:
            escalated = run_model(data, escalate=True, deadline=deadline, country=country)
        except AllModelsFailed as error:
            # The first answer was valid, so it is kept rather than reporting no answer.
            logger.warning("Triage: escalation failed, keeping the first answer: %s", error)
            result = dataclasses.replace(add_failed_usage(result, error), escalated=True)
        else:
            # Model fields = the model whose answer is shown; tokens and cost = every call.
            escalated_answer = parse_answer(escalated.text)
            if escalated_answer is None:
                logger.warning("Triage: escalation answer is invalid, keeping the first answer")
                result = add_usage(result, escalated, shown=result)
            else:
                # The escalation model may raise the risk, never lower it. Same level: its answer is used.
                risk_level = highest_risk([first_answer["risk_level"], escalated_answer["risk_level"]])
                if escalated_answer["risk_level"] == risk_level:
                    answer, result = escalated_answer, add_usage(result, escalated, shown=escalated)
                else:
                    result = add_usage(result, escalated, shown=result)

>>>>>>> Stashed changes
>>>>>>> v2_main
    return MotorOutput(
        risk_level=risk_level,
        confidence_score=confidence,
        explanation=explanation,
        disclaimer_id=DISCLAIMER_ID,
<<<<<<< HEAD
        finding_type=finding_type,
        evidence_level="stub",
        **usage_fields(result),  # model name, tokens and cost; "stub" and 0 when USE_BEDROCK is off
=======
<<<<<<< Updated upstream
        finding_type="symptom_keywords",
        evidence_level="stub",
        model_name="stub",  # no AI model yet; token and cost fields keep their default of 0
=======
        finding_type=answer["finding_type"] or "ai_triage",
        # The check after the AI answer: the source id must be in the trusted list for this country and motor.
        # Matched: the library's citation and link are attached. Otherwise: "pending_medical_review", no reference.
        **check_source(answer["source_id"], MOTOR, country),
        **usage_fields(result),  # model name, tokens and cost of every call; "stub" and 0 when USE_BEDROCK is off
>>>>>>> Stashed changes
>>>>>>> v2_main
    )
