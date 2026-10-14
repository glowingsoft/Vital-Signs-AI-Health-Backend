# The worker loop: reads jobs from the Redis Streams and saves them to Postgres (handlers.py).
# A job is ACKed (removed from the pending list) only after its database transaction has committed.

import asyncio
import json
import logging
import os
import signal
import socket
import time

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from app import config
from app.shared.db import close_db
from app.shared.jobs import AuditJob, SaveAssessmentJob
from app.shared.redis_client import close_redis, get_redis
from app.worker.handlers import handle_audit, handle_save_assessment

logger = logging.getLogger("worker")

# For each stream: the Pydantic model of its jobs, and the function that saves one job.
HANDLERS = {
    config.STREAM_SAVE_ASSESSMENT: (SaveAssessmentJob, handle_save_assessment),
    config.STREAM_AUDIT: (AuditJob, handle_audit),
}

# A unique name for this worker process, so Redis knows which worker holds which job.
CONSUMER_NAME = f"{socket.gethostname()}-{os.getpid()}"

BLOCK_MS = 5000  # how long XREADGROUP waits for new jobs before returning empty
BATCH_SIZE = 10  # max jobs taken from Redis in one call
# A job nobody ACKed for this long is taken over and run again. That covers two cases:
# the job failed (we did not ACK it), or the worker that had it crashed.
RETRY_AFTER_MS = 60_000


async def create_groups(redis: Redis) -> None:
    for stream in HANDLERS:
        try:
            # mkstream=True creates the stream if it does not exist yet. id="0" = start from its first message.
            await redis.xgroup_create(stream, config.CONSUMER_GROUP, id="0", mkstream=True)
        except ResponseError as error:
            if "BUSYGROUP" not in str(error):  # BUSYGROUP = the group already exists, which is fine
                raise


async def replay_failed_enqueues(redis: Redis) -> None:
    """Move jobs the API wrote to FAILED_ENQUEUE_FILE (while Redis was down) into Redis."""
    path = config.FAILED_ENQUEUE_FILE
    replay_path = path + ".replaying"
    if not os.path.exists(replay_path):
        if not os.path.exists(path):
            return
        # Rename first, so jobs the API writes while we replay go to a new file and are not lost.
        os.replace(path, replay_path)

    with open(replay_path) as file:
        lines = [line for line in file if line.strip()]
    for line in lines:
        item = json.loads(line)
        await redis.xadd(item["stream"], {"data": item["data"]})
    # If we crash before this line, the file is replayed again next time. Jobs are safe to save twice.
    os.remove(replay_path)
    logger.info("Replayed %d job(s) from %s", len(lines), path)


async def get_attempt_count(redis: Redis, stream: str, message_id: str) -> int:
    """How many times Redis has handed out this message (XPENDING 'times_delivered')."""
    pending = await redis.xpending_range(stream, config.CONSUMER_GROUP, min=message_id, max=message_id, count=1)
    return pending[0]["times_delivered"] if pending else 1


async def move_to_dead(redis: Redis, stream: str, message_id: str, data: str, error: Exception, attempts: int) -> None:
    # Keep the job and its last error in "jobs:dead" so someone can look at it and replay it by hand.
    await redis.xadd(
        config.STREAM_DEAD,
        {"stream": stream, "message_id": message_id, "data": data, "error": repr(error), "attempts": attempts},
    )
    await redis.xack(stream, config.CONSUMER_GROUP, message_id)  # stop retrying the original


async def process_message(redis: Redis, stream: str, message_id: str, fields: dict) -> None:
    started = time.monotonic()
    data = fields.get("data", "")
    job_model, handler = HANDLERS[stream]
    job_id = "unknown"
    try:
        job = job_model.model_validate_json(data)  # JSON string -> Pydantic job (validated)
        job_id = job.job_id
        await handler(job)
        # ACK only after the commit. If the worker dies before this line, the job is simply run again.
        await redis.xack(stream, config.CONSUMER_GROUP, message_id)
        result = "saved"
        level = logging.INFO
    except Exception as error:
        # No ACK: the job stays pending and is retried after RETRY_AFTER_MS.
        attempts = await get_attempt_count(redis, stream, message_id)
        if attempts >= config.MAX_JOB_ATTEMPTS:
            await move_to_dead(redis, stream, message_id, data, error, attempts)
            result = f"moved to {config.STREAM_DEAD} after {attempts} attempts: {error!r}"
        else:
            result = f"failed (attempt {attempts}/{config.MAX_JOB_ATTEMPTS}), will retry: {error!r}"
        level = logging.WARNING

    took_ms = int((time.monotonic() - started) * 1000)
    logger.log(level, "stream=%s job_id=%s result=%s time_ms=%d", stream, job_id, result, took_ms)


async def reclaim_stuck_jobs(redis: Redis, stop: asyncio.Event) -> None:
    """Take over and run jobs that were not ACKed for RETRY_AFTER_MS (failed, or their worker crashed)."""
    for stream in HANDLERS:
        # XAUTOCLAIM moves those jobs to this worker. Each claim adds 1 to the job's delivery count,
        # which is how we count attempts.
        response = await redis.xautoclaim(
            stream, config.CONSUMER_GROUP, CONSUMER_NAME, min_idle_time=RETRY_AFTER_MS, count=BATCH_SIZE
        )
        messages = response[1]
        for message_id, fields in messages:
            if stop.is_set():
                return
            await process_message(redis, stream, message_id, fields or {})


async def read_new_jobs(redis: Redis, stop: asyncio.Event) -> None:
    # XREADGROUP waits up to BLOCK_MS for new jobs on both streams. ">" = only jobs never given to any worker.
    response = await redis.xreadgroup(
        config.CONSUMER_GROUP, CONSUMER_NAME, {stream: ">" for stream in HANDLERS}, count=BATCH_SIZE, block=BLOCK_MS
    )
    for stream, messages in response or []:
        for message_id, fields in messages:
            if stop.is_set():
                return  # jobs not run yet stay pending and are reclaimed later
            await process_message(redis, stream, message_id, fields)


async def run() -> None:
    # Ctrl+C (SIGINT) or SIGTERM (e.g. a container stopping) only sets this flag.
    # The loop checks it between jobs, so the job in progress always finishes first.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    redis = get_redis()
    await create_groups(redis)
    await replay_failed_enqueues(redis)
    logger.info("Worker %s started, reading %s", CONSUMER_NAME, ", ".join(HANDLERS))

    while not stop.is_set():
        try:
            await reclaim_stuck_jobs(redis, stop)
            await read_new_jobs(redis, stop)
        except Exception as error:
            # For example Redis restarting. Wait a moment and try again instead of crashing.
            logger.error("Worker loop error, retrying in 2 s: %r", error)
            await asyncio.sleep(2)

    logger.info("Worker %s stopping", CONSUMER_NAME)
    await close_redis()
    await close_db()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run())
