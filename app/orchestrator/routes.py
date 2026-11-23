# The orchestrator endpoint POST /ai/assess: decides which motors are needed, calls them
# in parallel over HTTP, combines their answers, applies the FDA wording rules and queues the save.

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app import config
from app.orchestrator.build_job import build_save_assessment_job
<<<<<<< Updated upstream
from app.orchestrator.consolidate import consolidate
=======
from app.orchestrator.consent_check import check_consent
from app.orchestrator.consolidate import consolidate, sources_for_app
>>>>>>> Stashed changes
from app.orchestrator.fda_filter import apply_fda_rules
from app.shared.contracts import AssessRequest, AssessResponse, MotorInput, MotorOutput
from app.shared.queue import enqueue
from python_auth.auth import AuthUser, require_roles  # verifies the NestJS access token
from python_auth.roles import UserType

logger = logging.getLogger(__name__)

router = APIRouter(tags=["orchestrator"])


def build_motor_inputs(request: AssessRequest, user_id: str) -> Dict[str, MotorInput]:
    """Turn the app's request into one MotorInput per needed motor. Missing parts = motor not called.
    user_id = the caller's id from the verified token, never from the request body."""
    now = datetime.now(timezone.utc)
    country = request.context.country if request.context else None  # picks the trusted medical sources
    inputs = {}
    if request.symptoms:
        inputs["triage"] = MotorInput(
<<<<<<< HEAD
            user_id=user_id, timestamp=now, input_type="text", data={"message": request.symptoms}
        )
    if request.image:
        inputs["visual"] = MotorInput(
            user_id=user_id, timestamp=now, input_type="image", data={"image": request.image}
=======
<<<<<<< Updated upstream
            user_id=request.user_id, timestamp=now, input_type="text", data={"message": request.symptoms}
        )
    if request.image:
        inputs["visual"] = MotorInput(
            user_id=request.user_id, timestamp=now, input_type="image", data={"image": request.image}
=======
            user_id=user_id, timestamp=now, input_type="text", data={"message": request.symptoms},
            country=country,
        )
    if request.image:
        inputs["visual"] = MotorInput(
            user_id=user_id, timestamp=now, input_type="image", data={"image": request.image},
            country=country,
>>>>>>> Stashed changes
>>>>>>> v2_main
        )
    if request.vitals:
        inputs["vitals"] = MotorInput(
            user_id=user_id,
            timestamp=now,
            input_type="vitals",
            # exclude_none = only send the readings the user actually has; mode="json" = dates become strings
            data=request.vitals.model_dump(mode="json", exclude_none=True),
            country=country,
        )
    return inputs


def make_http_client() -> httpx.AsyncClient:
    # Kept in its own function so tests can swap in a client that talks to the app directly.
    # httpx's own default timeout is 5 s, shorter than the visual limit: give it a little more than the slowest
    # motor, so each motor's limit is decided by call_motor() (config.MOTOR_TIMEOUTS), not by httpx.
    return httpx.AsyncClient(timeout=config.longest_motor_timeout_seconds() + 1)


def elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


async def call_motor(
    client: httpx.AsyncClient, name: str, motor_input: MotorInput
) -> Tuple[Optional[MotorOutput], int]:
    """Call one motor. Returns (its answer or None if it failed or was too slow, time taken in ms)."""
    url = f"{config.MOTOR_URLS[name]}/motor/{name}/analyze"
    started = time.monotonic()  # monotonic = a clock that never jumps, made for measuring durations
    try:
        # wait_for = give up after this motor's limit (config.MOTOR_TIMEOUTS) for the WHOLE call (connect + answer).
        response = await asyncio.wait_for(
            # mode="json" turns the datetime into a string so it can be sent as JSON.
            client.post(url, json=motor_input.model_dump(mode="json")),
            timeout=config.motor_timeout_seconds(name),
        )
        response.raise_for_status()  # treat 4xx/5xx answers as failures
        answer = MotorOutput(**response.json())  # also validates that the motor kept the contract
        return answer, elapsed_ms(started)
    except Exception as error:
        # One broken motor must not break the whole request, so we log it and move on.
        logger.warning("Motor %s failed: %r", name, error)
        return None, elapsed_ms(started)


# async def = this view can wait on network calls without blocking other requests.
# require_roles = 401 without a valid NestJS access token, 403 unless the caller is a patient (UserType.User).
@router.post("/ai/assess", response_model=AssessResponse)
async def assess(request: AssessRequest, user: AuthUser = Depends(require_roles(UserType.User))) -> dict:
    started = time.monotonic()
    motor_inputs = build_motor_inputs(request, user.user_id)
    if not motor_inputs:
        # HTTPException = FastAPI's way to return an error response (like raising ValidationError in DRF).
        raise HTTPException(status_code=400, detail="Send at least one of: symptoms, image, vitals.")

    names = list(motor_inputs)
    async with make_http_client() as client:
        # gather = run all motor calls at the same time and wait until every one has finished.
        calls = await asyncio.gather(*(call_motor(client, name, motor_inputs[name]) for name in names))

    # zip pairs each motor name with its (answer, latency); they come back in the same order as names.
    results = {}
    latencies = {}
    failed = []
    for name, (answer, latency_ms) in zip(names, calls):
        if answer is None:
            failed.append(name)
        else:
            results[name] = answer
            latencies[name] = latency_ms

    response = consolidate(results)
    response["motors_used"] = list(results)
    response["motors_failed"] = failed
    response = apply_fda_rules(response)

    # Queue everything for the worker to save. We do NOT wait for the database: XADD takes about
    # a millisecond, and if Redis is down enqueue() writes to a local file instead of failing.
    prediction_id = uuid4()
    job = build_save_assessment_job(
        prediction_id, request, response, results, latencies, elapsed_ms(started), user_id=user.user_id
    )
    await enqueue(config.STREAM_SAVE_ASSESSMENT, job)

    # The source of each motor's answer, for the patient: label and library link only, never the full citation.
    # Added after the FDA filter and the save job, so neither changes (the label is not part of "explanation").
    response["sources"] = sources_for_app(results, request.context.country if request.context else None)
    response["prediction_id"] = prediction_id
    return response
