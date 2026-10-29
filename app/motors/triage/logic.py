# Triage motor: the risk level comes only from the routed AI model (app/motors/triage/model.py).
# The default model answers first; a high or critical answer, or low confidence, is checked again
# by the escalation model, which can raise the risk level but never lower it.
# With USE_BEDROCK off, or when no model answers, the "ai_unavailable" result is used.

import dataclasses
import logging
import time

from app import config
from app.configurations.routing import TRIAGE_ESCALATION_CONFIDENCE
from app.motors.runner import (
    AllModelsFailed, ModelResult, add_failed_usage, add_usage, parse_answer, stub_result, usage_fields,
)
from app.motors.triage.model import run_model
from app.shared.contracts import DISCLAIMER_ID, MotorOutput, highest_risk

logger = logging.getLogger(__name__)

# Never "low": without an AI answer we cannot say the symptoms are mild.
UNAVAILABLE_EXPLANATION = "We could not complete the check right now. Please contact a healthcare professional."


def needs_escalation(answer: dict) -> bool:
    return answer["risk_level"] in ("high", "critical") or answer["confidence_score"] < TRIAGE_ESCALATION_CONFIDENCE


def unavailable_output(result: ModelResult) -> MotorOutput:
    return MotorOutput(
        risk_level="medium",
        confidence_score=0.0,
        explanation=UNAVAILABLE_EXPLANATION,
        disclaimer_id=DISCLAIMER_ID,
        finding_type="ai_unavailable",
        evidence_level="stub",
        **usage_fields(result),
    )


def analyze(data: dict) -> MotorOutput:
    # One time budget for every call: default model, retries, fallbacks and escalation.
    deadline = time.monotonic() + config.MOTOR_TIMEOUT_SECONDS
    try:
        result = run_model(data, deadline=deadline)
    except AllModelsFailed as error:
        logger.warning("Triage: no AI answer: %s", error)
        return unavailable_output(add_failed_usage(stub_result(), error))

    answer = parse_answer(result.text)  # None only for the stub: run_chain checks the JSON
    if answer is None:
        return unavailable_output(result)

    if needs_escalation(answer):
        first_answer = answer
        try:
            escalated = run_model(data, escalate=True, deadline=deadline)
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

    return MotorOutput(
        risk_level=answer["risk_level"],
        confidence_score=answer["confidence_score"],
        explanation=answer["explanation"],
        disclaimer_id=DISCLAIMER_ID,
        finding_type="ai_triage",
        evidence_level="stub",
        **usage_fields(result),  # model name, tokens and cost of every call; "stub" and 0 when USE_BEDROCK is off
    )
