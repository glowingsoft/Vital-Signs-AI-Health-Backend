# The INSERT statements for the "ai" tables, one small function per table.
# Plain SQL with named parameters (:name); values are sent separately, never pasted into the SQL.

import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.jobs import AiUsageRow, AssessmentRow, AuditLogRow, MotorResultRow, ReadingRow


def to_json(value):
    # asyncpg sends jsonb columns as a JSON string, so dicts are converted here. None stays NULL.
    return None if value is None else json.dumps(value)


# ---------- ai.vitals_readings ----------

INSERT_READING = text("""
    INSERT INTO ai.vitals_readings (
        id, user_id, client_reading_id, measured_at, received_at,
        heart_rate, systolic_bp, diastolic_bp, respiratory_rate, spo2, hrv_ms, sleep_minutes,
        label, label_source, model_version, source, recorded_offline,
        device_platform, device_model, os_version, app_version, state, country, created_at
    ) VALUES (
        :id, :user_id, :client_reading_id, :measured_at, :received_at,
        :heart_rate, :systolic_bp, :diastolic_bp, :respiratory_rate, :spo2, :hrv_ms, :sleep_minutes,
        :label, :label_source, :model_version, :source, :recorded_offline,
        :device_platform, :device_model, :os_version, :app_version, :state, :country, :created_at
    )
    ON CONFLICT (user_id, client_reading_id) DO NOTHING
    RETURNING id
""")

SELECT_READING_ID = text("""
    SELECT id FROM ai.vitals_readings
    WHERE user_id = :user_id AND client_reading_id = :client_reading_id
""")


async def insert_reading(session: AsyncSession, row: ReadingRow) -> UUID:
    """Insert the reading and return its id. If the phone already sent it, return the existing id."""
    # model_dump() turns the Pydantic row into a dict whose keys match the :names above.
    result = await session.execute(INSERT_READING, row.model_dump())
    new_id = result.scalar_one_or_none()  # None when ON CONFLICT skipped the insert
    if new_id is not None:
        return new_id
    result = await session.execute(
        SELECT_READING_ID, {"user_id": row.user_id, "client_reading_id": row.client_reading_id}
    )
    return result.scalar_one()


# ---------- ai.assessments ----------

INSERT_ASSESSMENT = text("""
    INSERT INTO ai.assessments (
        id, user_id, reading_id, input_types, input,
        risk_level, confidence_score, explanation, label, label_source, disclaimer_id,
        motors_used, motors_failed, total_latency_ms, app_version, state, country, created_at
    ) VALUES (
        :id, :user_id, :reading_id, :input_types, :input,
        :risk_level, :confidence_score, :explanation, :label, :label_source, :disclaimer_id,
        :motors_used, :motors_failed, :total_latency_ms, :app_version, :state, :country, :created_at
    )
    ON CONFLICT (id) DO NOTHING
""")


async def insert_assessment(session: AsyncSession, row: AssessmentRow) -> None:
    params = row.model_dump()
    params["input"] = to_json(params["input"])  # jsonb
    # input_types, motors_used and motors_failed are Python lists: asyncpg sends them as text[] directly.
    await session.execute(INSERT_ASSESSMENT, params)


# ---------- ai.motor_results ----------

INSERT_MOTOR_RESULT = text("""
    INSERT INTO ai.motor_results (
        id, assessment_id, motor, risk_level, confidence_score, explanation,
        finding_type, evidence_level, reference,
        model_name, model_version, escalated, fallback_used, latency_ms, created_at
    ) VALUES (
        :id, :assessment_id, :motor, :risk_level, :confidence_score, :explanation,
        :finding_type, :evidence_level, :reference,
        :model_name, :model_version, :escalated, :fallback_used, :latency_ms, :created_at
    )
    ON CONFLICT (id) DO NOTHING
""")


async def insert_motor_result(session: AsyncSession, row: MotorResultRow) -> None:
    await session.execute(INSERT_MOTOR_RESULT, row.model_dump())


# ---------- ai.ai_usage ----------

INSERT_AI_USAGE = text("""
    INSERT INTO ai.ai_usage (
        id, assessment_id, user_id, motor, model_name, aws_region,
        input_tokens, output_tokens, reasoning_tokens, image_count,
        is_escalation, is_fallback, is_retry, cost_usd, latency_ms, created_at
    ) VALUES (
        :id, :assessment_id, :user_id, :motor, :model_name, :aws_region,
        :input_tokens, :output_tokens, :reasoning_tokens, :image_count,
        :is_escalation, :is_fallback, :is_retry, :cost_usd, :latency_ms, :created_at
    )
    ON CONFLICT (id) DO NOTHING
""")


async def insert_ai_usage(session: AsyncSession, row: AiUsageRow) -> None:
    await session.execute(INSERT_AI_USAGE, row.model_dump())


# ---------- ai.audit_log ----------

INSERT_AUDIT_LOG = text("""
    INSERT INTO ai.audit_log (
        id, actor_id, actor_role, action, resource_type, resource_id, patient_id,
        reason, before, after, service, ip_address, created_at
    ) VALUES (
        :id, :actor_id, :actor_role, :action, :resource_type, :resource_id, :patient_id,
        :reason, :before, :after, :service, :ip_address, :created_at
    )
    ON CONFLICT (id) DO NOTHING
""")


async def insert_audit_log(session: AsyncSession, row: AuditLogRow) -> None:
    params = row.model_dump()
    params["before"] = to_json(params["before"])  # jsonb
    params["after"] = to_json(params["after"])  # jsonb
    await session.execute(INSERT_AUDIT_LOG, params)
