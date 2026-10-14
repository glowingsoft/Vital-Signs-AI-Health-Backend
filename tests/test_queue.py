# Tests for app/shared/queue.py: jobs go to Redis, or to the fallback file when Redis is down.
# Redis is replaced by a mock, so these tests need no Redis server.

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from app import config
from app.shared import queue
from app.shared.jobs import AuditJob, AuditLogRow


def make_job() -> AuditJob:
    return AuditJob(
        audit_log=AuditLogRow(actor_id="u_1", actor_role="patient", action="view", resource_type="test", reason="t")
    )


def fake_redis(xadd: AsyncMock) -> MagicMock:
    redis = MagicMock()
    redis.xadd = xadd
    return redis


def test_enqueue_writes_fallback_file_when_redis_is_down(monkeypatch, tmp_path):
    fallback_file = tmp_path / "failed_enqueue.jsonl"
    monkeypatch.setattr(config, "FAILED_ENQUEUE_FILE", str(fallback_file))
    broken = fake_redis(AsyncMock(side_effect=ConnectionError("Redis is down")))
    monkeypatch.setattr(queue, "get_redis", lambda: broken)
    job = make_job()

    message_id = asyncio.run(queue.enqueue("jobs:audit", job))  # must not raise

    assert message_id is None
    lines = fallback_file.read_text().splitlines()
    assert len(lines) == 1
    saved = json.loads(lines[0])
    assert saved["stream"] == "jobs:audit"
    assert json.loads(saved["data"])["job_id"] == str(job.job_id)


def test_enqueue_returns_message_id_when_redis_is_up(monkeypatch, tmp_path):
    fallback_file = tmp_path / "failed_enqueue.jsonl"
    monkeypatch.setattr(config, "FAILED_ENQUEUE_FILE", str(fallback_file))
    working = fake_redis(AsyncMock(return_value="1700000000000-0"))
    monkeypatch.setattr(queue, "get_redis", lambda: working)

    message_id = asyncio.run(queue.enqueue("jobs:audit", make_job()))

    assert message_id == "1700000000000-0"
    assert not fallback_file.exists()
