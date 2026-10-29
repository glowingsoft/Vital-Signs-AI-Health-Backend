# Visual motor: the routed AI model describes the image (app/motors/visual/model.py).
# Haiku answers first (asked again up to VISUAL_MAX_JSON_RETRIES times for invalid JSON). Sonnet answers
# when Haiku fails, then Nova Pro; Sonnet checks again when Haiku is unsure and can raise the risk level,
# never lower it. With USE_BEDROCK off, or when no model answers, the "ai_unavailable" result is used.
# An image that is not a readable JPEG, PNG, GIF or WEBP raises InvalidImage (HTTP 422, see routes.py).

import dataclasses
import logging
import time

from app import config
from app.configurations.routing import VISUAL_ESCALATION_CONFIDENCE
from app.motors.runner import (
    AllModelsFailed, ModelResult, add_failed_usage, add_usage, parse_answer, stub_result, usage_fields,
)
from app.motors.visual.model import run_model
from app.shared.contracts import DISCLAIMER_ID, MotorOutput, highest_risk

logger = logging.getLogger(__name__)

# Never "low": without an AI answer we cannot say the image looks fine.
UNAVAILABLE_EXPLANATION = (
    "Our AI is busy right now and could not check your image. Please try again in a few minutes, "
    "or ask a doctor to review it."
)


def unavailable_output(result: ModelResult) -> MotorOutput:
    return MotorOutput(
        risk_level="medium",
        confidence_score=0.0,
        explanation=UNAVAILABLE_EXPLANATION,
        disclaimer_id=DISCLAIMER_ID,
        finding_type="ai_unavailable",
        evidence_level="stub",
        **usage_fields(result),  # calls that answered off-format still cost money
    )


def analyze(data: dict) -> MotorOutput:
    if not data.get("image"):
        return MotorOutput(
            risk_level="low",
            confidence_score=0.1,
            explanation="No image was received, so no visual check was made.",
            disclaimer_id=DISCLAIMER_ID,
            model_name="stub",  # no AI model yet; token and cost fields keep their default of 0
        )

    # One time budget for every call: Haiku, its retries, Sonnet and Nova Pro.
    deadline = time.monotonic() + config.MOTOR_TIMEOUT_SECONDS
    try:
        result = run_model(data, deadline=deadline, use_fallbacks=False)  # Haiku only
    except AllModelsFailed as error:
        # Haiku failed: Sonnet answers instead, then Nova Pro.
        logger.warning("Visual: Haiku failed, trying the backup models: %s", error)
        spent = add_failed_usage(stub_result(), error)
        try:
            backup = run_model(data, escalate=True, deadline=deadline)
        except AllModelsFailed as backup_error:
            logger.warning("Visual: no AI answer: %s", backup_error)
            return unavailable_output(add_failed_usage(spent, backup_error))
        # A backup for a failed Haiku, not an escalation: Sonnet was not asked to check an answer.
        result = dataclasses.replace(add_failed_usage(backup, error), escalated=False, fallback_used=True)

    answer = parse_answer(result.text)  # None only for the stub: run_chain checks the JSON
    if answer is None:
        return unavailable_output(result)

    if result.model_key == "haiku" and answer["confidence_score"] < VISUAL_ESCALATION_CONFIDENCE:
        # Haiku is unsure: Sonnet checks again. Model fields = the model whose answer is shown; cost = every call.
        try:
            escalated = run_model(data, escalate=True, deadline=deadline, use_fallbacks=False)
        except AllModelsFailed as error:
            logger.warning("Visual: escalation failed, keeping Haiku's answer: %s", error)
            result = dataclasses.replace(add_failed_usage(result, error), escalated=True)
        else:
            escalated_answer = parse_answer(escalated.text)  # run_chain checks the JSON; None is only a guard
            if escalated_answer is not None and escalated_answer["risk_level"] == highest_risk(
                [answer["risk_level"], escalated_answer["risk_level"]]
            ):
                # Sonnet's level is the higher one, or the same: its answer is used.
                answer, result = escalated_answer, add_usage(result, escalated, shown=escalated)
            else:
                result = add_usage(result, escalated, shown=result)

    return MotorOutput(
        risk_level=answer["risk_level"],
        confidence_score=answer["confidence_score"],
        explanation=answer["explanation"],
        disclaimer_id=DISCLAIMER_ID,
        finding_type="image_description",
        evidence_level="stub",
        **usage_fields(result),
    )
