# Shared by the three motors' model.py files: try the routed models in order, measure the cost,
# and turn the result into MotorOutput fields. The per-motor choices live in app/configurations/.

import dataclasses
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
    marks it as failed (triage instead answers its "ai_unavailable" result)."""

    def __init__(
        self,
        message: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        latency_ms: int = 0,
        image_count: int = 0,
    ):
        super().__init__(message)
        # What the calls that did answer (with invalid JSON) cost, so it can still be recorded.
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cost_usd = cost_usd
        self.latency_ms = latency_ms  # wall-clock time of the whole chain, failed calls included
        self.image_count = image_count  # answered calls that sent an image


@dataclass(frozen=True)
class ModelResult:
    text: str  # "" for the stub: the motor then keeps its rule-based answer
    model_key: str
    model_id: str
    model_version: Optional[str]
    escalated: bool  # the escalation model was requested
    fallback_used: bool  # a fallback model answered instead of the first choice
    is_retry: bool  # same model asked again after an invalid JSON answer
    input_tokens: int
    output_tokens: int
    image_count: int  # answered calls that sent an image (an image-free JSON retry does not count)
    cost_usd: float
    latency_ms: int  # wall-clock time of the whole chain, failed and timed-out calls included


def stub_result() -> ModelResult:
    """What run_model() returns when USE_BEDROCK is off: no AI call, no cost."""
    return ModelResult(
        text="", model_key="stub", model_id="stub", model_version=None, escalated=False, fallback_used=False,
        is_retry=False, input_tokens=0, output_tokens=0, image_count=0, cost_usd=0.0, latency_ms=0,
    )


# Sent once to the same model when its answer is not valid JSON (json_answer=True).
RETRY_MESSAGE = "Your previous reply was not valid JSON. Reply with JSON only in the required format."


def run_chain(
    motor: str,
    first: str,
    messages: List[dict],
    system: str,
    max_tokens: int,
    escalated: bool = False,
    image_count: int = 0,
    json_answer: bool = False,
    deadline: Optional[float] = None,
    max_json_retries: int = 1,
    use_fallbacks: bool = True,
    retry_without_images: bool = False,
) -> ModelResult:
    """Call `first`, then each fallback, until one answers. All attempts together stay within
    MOTOR_TIMEOUT_SECONDS (or until `deadline`, a time.monotonic() value shared by several chains),
    so the motor answers before the orchestrator gives up on it.
    With json_answer=True an answer parse_answer() rejects is asked for again from the same model, up to
    max_json_retries times, then the next model is tried. use_fallbacks=False tries only `first`.
    retry_without_images=True leaves the images out of a retry that has a reply to reformat.
    The tokens, cost and images of every answered call are added up."""
    chain = model_chain(motor, first, has_image=image_count > 0)
    if not use_fallbacks:
        chain = chain[:1]
    started = time.monotonic()
    if deadline is None:
        deadline = started + config.MOTOR_TIMEOUT_SECONDS
    errors = []
    input_tokens = output_tokens = images_sent = 0
    cost = 0.0
    for attempt, key in enumerate(chain):
        conversation = messages
        for call in range(1 + max_json_retries if json_answer else 1):
            is_retry = call > 0
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                errors.append(f"{key}: no time left")
                break
            try:
                answer = bedrock.converse(key, conversation, system, max_tokens, timeout=remaining)
            except bedrock.BedrockError as error:
                logger.warning("Motor %s: model %s failed: %s", motor, key, error)
                errors.append(str(error))
                break
            input_tokens += answer.input_tokens
            output_tokens += answer.output_tokens
            images_sent += sends_image(conversation)
            cost += cost_usd(key, answer.input_tokens, answer.output_tokens)
            if json_answer and parse_answer(answer.text) is None:
                logger.warning("Motor %s: model %s gave invalid JSON (retry=%s)", motor, key, is_retry)
                errors.append(f"{key}: invalid JSON")
                conversation = retry_conversation(messages, answer.text, drop_images=retry_without_images)
                continue
            model = MODELS[key]
            return ModelResult(
                text=answer.text,
                model_key=key,
                model_id=model.model_id,
                model_version=model.version,
                escalated=escalated,
                fallback_used=attempt > 0,
                is_retry=is_retry,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                image_count=images_sent,
                cost_usd=cost,
                latency_ms=elapsed_ms(started),
            )
        if deadline - time.monotonic() <= 0:
            break
    raise AllModelsFailed(
        f"{motor}: every model failed ({'; '.join(errors)})",
        input_tokens, output_tokens, cost, elapsed_ms(started), images_sent,
    )


def elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def sends_image(messages: List[dict]) -> bool:
    return any("image" in block for message in messages for block in message["content"])


def retry_conversation(messages: List[dict], bad_reply: str, drop_images: bool = False) -> List[dict]:
    """The conversation plus the invalid reply and RETRY_MESSAGE. Converse refuses an empty assistant turn,
    so an empty reply only adds RETRY_MESSAGE to the last user turn (images kept: there is nothing to reformat).
    drop_images=True leaves the images out when there is a reply: fixing its format needs only the reply."""
    if bad_reply.strip():
        retry_message = RETRY_MESSAGE
        if drop_images:
            messages = [
                {**message, "content": [block for block in message["content"] if "image" not in block]}
                for message in messages
            ]
            retry_message += " Use only your previous reply; do not describe anything new."
        return messages + [
            {"role": "assistant", "content": [{"text": bad_reply}]},
            {"role": "user", "content": [{"text": retry_message}]},
        ]
    last = messages[-1]
    return messages[:-1] + [{**last, "content": last["content"] + [{"text": RETRY_MESSAGE}]}]


def add_usage(first: ModelResult, escalated: ModelResult, shown: ModelResult) -> ModelResult:
    """`shown` (first or escalated: the result whose answer the user sees) with the tokens, images, cost and
    latency of both calls."""
    return dataclasses.replace(
        shown,
        escalated=True,
        is_retry=first.is_retry or escalated.is_retry,
        input_tokens=first.input_tokens + escalated.input_tokens,
        output_tokens=first.output_tokens + escalated.output_tokens,
        image_count=first.image_count + escalated.image_count,
        cost_usd=first.cost_usd + escalated.cost_usd,
        latency_ms=first.latency_ms + escalated.latency_ms,
    )


def add_failed_usage(result: ModelResult, error: AllModelsFailed) -> ModelResult:
    """`result` plus what the failed chain spent: invalid answers and the time of every attempt."""
    return dataclasses.replace(
        result,
        input_tokens=result.input_tokens + error.input_tokens,
        output_tokens=result.output_tokens + error.output_tokens,
        image_count=result.image_count + error.image_count,
        cost_usd=result.cost_usd + error.cost_usd,
        latency_ms=result.latency_ms + error.latency_ms,
    )


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
