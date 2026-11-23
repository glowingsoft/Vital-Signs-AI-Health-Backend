# The one place that calls Amazon Bedrock. Every motor goes through converse() here.
# Uses the Converse API, which has the same request and response shape for Claude, Nova and Llama.

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import List, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app import config
from app.configurations.models import MODELS

logger = logging.getLogger(__name__)


class BedrockError(Exception):
    """The model call failed or timed out. The caller tries the next model."""


@dataclass(frozen=True)
class ConverseResult:
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


_client = None

# boto3 calls block, so each one runs in a worker thread and we stop waiting at the deadline.
# A thread we stopped waiting for ends on its own within read_timeout below.
_executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="bedrock")


def get_client():
    """One shared bedrock-runtime client, created on first use (boto3 clients are thread-safe)."""
    global _client
    if _client is None:
        _client = boto3.client(
            "bedrock-runtime",
            region_name=config.AWS_REGION,
            config=Config(
                connect_timeout=1,
                read_timeout=config.MOTOR_TIMEOUT_SECONDS,
                # No silent retries inside boto3: a failed model goes straight to our fallback list.
                retries={"total_max_attempts": 1},
            ),
        )
    return _client


def _call(model_id: str, messages: List[dict], system: str, max_tokens: int) -> dict:
    request = {"modelId": model_id, "messages": messages, "inferenceConfig": {"maxTokens": max_tokens}}
    if system:
        request["system"] = [{"text": system}]
    return get_client().converse(**request)


def converse(
    model_key: str, messages: List[dict], system: str, max_tokens: int, timeout: Optional[float] = None
) -> ConverseResult:
    """Send one conversation to one model. Raises BedrockError on any failure or after `timeout` seconds
    (default MOTOR_TIMEOUT_SECONDS). messages use the Converse format: [{"role": "user", "content": [...]}]."""
    model = MODELS[model_key]
    timeout = config.MOTOR_TIMEOUT_SECONDS if timeout is None else timeout
    started = time.monotonic()
    future = _executor.submit(_call, model.model_id, messages, system, max_tokens)
    try:
        response = future.result(timeout=timeout)
    except FutureTimeout:
        future.cancel()
        raise BedrockError(f"{model_key} timed out after {timeout:.2f}s") from None
    except (BotoCoreError, ClientError) as error:
        raise BedrockError(f"{model_key} failed: {error}") from error

    content = response["output"]["message"]["content"]
    usage = response.get("usage", {})
    return ConverseResult(
        text="".join(block.get("text", "") for block in content),
        input_tokens=usage.get("inputTokens", 0),
        output_tokens=usage.get("outputTokens", 0),
        latency_ms=int((time.monotonic() - started) * 1000),
    )
