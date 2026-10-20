# Shared by the three motors' model.py files: try the routed models in order, measure the cost,
# and turn the result into MotorOutput fields. The per-motor choices live in app/configurations/.

import json
import logging
import time
from dataclasses import dataclass
from typing import List, Optional

from app import config
from app.configurations.models import MODELS
from app.configurations.pricing import cost_usd
from app.configurations.routing import model_chain
from app.shared import bedrock
from app.shared.contracts import RISK_ORDER

logger = logging.getLogger(__name__)


class AllModelsFailed(Exception):
    """Every model in the chain failed or the time ran out. The motor answers 500 and the orchestrator
    marks it as failed."""


@dataclass(frozen=True)
class ModelResult:
    text: str  # "" for the stub: the motor then keeps its rule-based answer
    model_key: str
    model_id: str
    model_version: Optional[str]
    escalated: bool  # the escalation model was requested
    fallback_used: bool  # a fallback model answered instead of the first choice
    is_retry: bool  # same model asked again (e.g. invalid JSON); not done yet, always False for now
    input_tokens: int
    output_tokens: int
    image_count: int
    cost_usd: float
    latency_ms: int


def stub_result() -> ModelResult:
    """What run_model() returns when USE_BEDROCK is off: no AI call, no cost."""
    return ModelResult(
        text="", model_key="stub", model_id="stub", model_version=None, escalated=False, fallback_used=False,
        is_retry=False, input_tokens=0, output_tokens=0, image_count=0, cost_usd=0.0, latency_ms=0,
    )


def run_chain(
    motor: str,
    first: str,
    messages: List[dict],
    system: str,
    max_tokens: int,
    escalated: bool = False,
    image_count: int = 0,
) -> ModelResult:
    """Call `first`, then each fallback, until one answers. All attempts together stay within
    MOTOR_TIMEOUT_SECONDS, so the motor answers before the orchestrator gives up on it."""
    chain = model_chain(motor, first, has_image=image_count > 0)
    deadline = time.monotonic() + config.MOTOR_TIMEOUT_SECONDS
    errors = []
    for attempt, key in enumerate(chain):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            errors.append(f"{key}: no time left")
            break
        try:
            answer = bedrock.converse(key, messages, system, max_tokens, timeout=remaining)
        except bedrock.BedrockError as error:
            logger.warning("Motor %s: model %s failed: %s", motor, key, error)
            errors.append(str(error))
            continue
        model = MODELS[key]
        return ModelResult(
            text=answer.text,
            model_key=key,
            model_id=model.model_id,
            model_version=model.version,
            escalated=escalated,
            fallback_used=attempt > 0,
            is_retry=False,
            input_tokens=answer.input_tokens,
            output_tokens=answer.output_tokens,
            image_count=image_count,
            cost_usd=cost_usd(key, answer.input_tokens, answer.output_tokens),
            latency_ms=answer.latency_ms,
        )
    raise AllModelsFailed(f"{motor}: every model failed ({'; '.join(errors)})")


def usage_fields(result: ModelResult) -> dict:
    """The MotorOutput fields filled from a ModelResult (saved to ai.motor_results and ai.ai_usage)."""
    return {
        "model_name": result.model_id,
        "model_version": result.model_version,
        "escalated": result.escalated,
        "fallback_used": result.fallback_used,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "image_count": result.image_count,
        "cost_usd": result.cost_usd,
    }


# Asked of the triage and visual models. Vitals only asks for a plain-text explanation.
JSON_ANSWER_INSTRUCTIONS = (
    'Reply with JSON only: {"risk_level": "low" | "medium" | "high" | "critical", '
    '"confidence_score": a number from 0 to 1, "explanation": "one or two short sentences"}. '
    "Never diagnose, never name a condition, medicine or specific medical action, and never sound certain: "
    'use words like "suggests" or "worth reviewing". Always recommend a healthcare professional.'
)


def parse_answer(text: str) -> Optional[dict]:
    """The model's JSON answer, or None when it is missing or does not follow the format."""
    text = text.strip()
    if text.startswith("```"):  # some models wrap JSON in a code fence
        text = text.strip("`").removeprefix("json")
    try:
        answer = json.loads(text)
        risk_level = answer["risk_level"]
        confidence = float(answer["confidence_score"])
        explanation = str(answer["explanation"]).strip()
    except (ValueError, KeyError, TypeError):
        return None
    if risk_level not in RISK_ORDER or not 0 <= confidence <= 1 or not explanation:
        return None
    return {"risk_level": risk_level, "confidence_score": confidence, "explanation": explanation}
