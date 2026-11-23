# Puts jobs on a Redis Stream for the worker to save. If Redis is down, the job is written to a
# local file instead (the worker replays it later), and the caller never sees an error.

import json
import logging
from typing import Optional

from pydantic import BaseModel

from app import config
from app.shared.jobs import AuditJob, AuditLogRow
from app.shared.redis_client import get_redis

logger = logging.getLogger(__name__)


async def enqueue(stream: str, job: BaseModel) -> Optional[str]:
    """Add the job to the stream. Returns the Redis message id, or None if it went to the fallback file."""
    data = job.model_dump_json()  # the whole job as one JSON string
    try:
        # XADD appends a message to the stream. "*" = let Redis pick the message id.
        return await get_redis().xadd(stream, {"data": data})
    except Exception as error:
        logger.error("Redis enqueue failed for %s, writing to %s: %r", stream, config.FAILED_ENQUEUE_FILE, error)
        save_to_fallback_file(stream, data)
        return None


def save_to_fallback_file(stream: str, data: str) -> None:
    """Append one JSON line: {"stream": ..., "data": ...}. The worker replays this file on start."""
    try:
        with open(config.FAILED_ENQUEUE_FILE, "a") as file:
            file.write(json.dumps({"stream": stream, "data": data}) + "\n")
    except Exception as error:
        # Last resort: the job is lost, so log loudly. We still do not raise: the user gets their answer.
        logger.critical("Could not write job to %s, job lost: %r", config.FAILED_ENQUEUE_FILE, error)


async def enqueue_audit(audit_row: AuditLogRow) -> Optional[str]:
    """Queue one standalone audit_log row (for example "someone viewed this data")."""
    return await enqueue(config.STREAM_AUDIT, AuditJob(audit_log=audit_row))
