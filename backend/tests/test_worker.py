# Runs handle_save_assessment twice with the same job against the real database and checks that
# each table has exactly one row. Skipped automatically when Postgres or Redis is not running.
# Note: rows are append-only, so test rows stay in the database (their user_id starts with "test_worker_").

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.orchestrator.build_job import build_save_assessment_job
from app.shared import db, redis_client
from app.shared.contracts import AssessRequest, MotorOutput
from app.worker.handlers import handle_save_assessment


async def services_are_up() -> bool:
    try:
        return await db.ping() and await redis_client.ping()
    finally:
        # Each asyncio.run() has its own event loop, so close connections made in this one.
        await db.close_db()
        await redis_client.close_redis()


def make_job():
    user_id = f"test_worker_{uuid4().hex[:8]}"
    request = AssessRequest(
        symptoms="headache",
        vitals={"heart_rate": 130, "client_reading_id": f"r_{uuid4().hex[:8]}"},
        context={"country": "US"},
    )
    output = MotorOutput(risk_level="high", confidence_score=0.8, explanation="raw", disclaimer_id="disc_v1")
    response = {
        "risk_level": "high",
        "confidence_score": 0.8,
        "explanation": "filtered",
        "disclaimer_id": "disc_v1",
        "motors_used": ["vitals"],
        "motors_failed": [],
    }
    return build_save_assessment_job(
        uuid4(), request, response, {"vitals": output}, {"vitals": 12}, 15, user_id=user_id
    )


async def count_rows(job) -> dict:
    queries = {
        "vitals_readings": ("SELECT count(*) FROM ai.vitals_readings WHERE user_id = :v", job.reading.user_id),
        "assessments": ("SELECT count(*) FROM ai.assessments WHERE id = :v", job.assessment.id),
        "motor_results": ("SELECT count(*) FROM ai.motor_results WHERE assessment_id = :v", job.assessment.id),
        "ai_usage": ("SELECT count(*) FROM ai.ai_usage WHERE assessment_id = :v", job.assessment.id),
        "audit_log": ("SELECT count(*) FROM ai.audit_log WHERE id = :v", job.audit_log.id),
    }
    counts = {}
    async with db.get_engine().connect() as connection:
        for table, (sql, value) in queries.items():
            counts[table] = (await connection.execute(text(sql), {"v": value})).scalar_one()
    return counts


async def save_twice_and_count(job) -> dict:
    try:
        await handle_save_assessment(job)
        await handle_save_assessment(job)  # a retried job: must not create duplicates or fail
        return await count_rows(job)
    finally:
        await db.close_db()


def test_same_job_twice_saves_one_row_per_table():
    if not asyncio.run(services_are_up()):
        pytest.skip("Postgres or Redis is not reachable")

    job = make_job()
    counts = asyncio.run(save_twice_and_count(job))

    assert counts == {"vitals_readings": 1, "assessments": 1, "motor_results": 1, "ai_usage": 1, "audit_log": 1}
