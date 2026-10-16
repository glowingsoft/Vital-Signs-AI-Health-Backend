# Turns one finished /ai/assess call into a SaveAssessmentJob: the rows the worker will save.
# Pure functions, no network or database, so they are easy to test.

from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import UUID

from app.shared.contracts import AssessRequest, MotorOutput, RequestContext
from app.shared.jobs import (
    AiUsageRow,
    AssessmentRow,
    AuditLogRow,
    MotorResultRow,
    ReadingRow,
    SaveAssessmentJob,
)


def pick_label(request: AssessRequest) -> str:
    # First label of the record: the user described symptoms, or only sent numbers.
    return "reported_symptom" if request.symptoms else "normal"


def input_types(request: AssessRequest) -> List[str]:
    types = []
    if request.symptoms:
        types.append("text")
    if request.image:
        types.append("image")
    if request.vitals:
        types.append("vitals")
    return types


def stored_input(request: AssessRequest) -> dict:
    """The request as saved in ai.assessments.input. Image bytes are never stored in the database."""
    data = request.model_dump(mode="json", exclude_none=True)  # mode="json" = dates become strings
    if "image" in data:
        data["image"] = "omitted"
    return data


def build_reading(request: AssessRequest, user_id: str, context: RequestContext) -> Optional[ReadingRow]:
    vitals = request.vitals
    if vitals is None:
        return None
    return ReadingRow(
        user_id=user_id,
        client_reading_id=vitals.client_reading_id,
        measured_at=vitals.measured_at or datetime.now(timezone.utc),
        heart_rate=vitals.heart_rate,
        systolic_bp=vitals.systolic,
        diastolic_bp=vitals.diastolic,
        respiratory_rate=vitals.respiratory_rate,
        spo2=vitals.spo2,
        label=pick_label(request),
        label_source="user",
        model_version=vitals.model_version,
        source=vitals.source,
        recorded_offline=vitals.recorded_offline,
        device_platform=context.device_platform,
        device_model=context.device_model,
        os_version=context.os_version,
        app_version=context.app_version,
        state=context.state,
        country=context.country,  # required with vitals (checked in AssessRequest)
    )


def build_motor_result(prediction_id: UUID, motor: str, output: MotorOutput, latency_ms: int) -> MotorResultRow:
    return MotorResultRow(
        assessment_id=prediction_id,
        motor=motor,
        risk_level=output.risk_level,
        confidence_score=output.confidence_score,
        explanation=output.explanation,  # the motor's own text, before the FDA filter
        finding_type=output.finding_type,
        evidence_level=output.evidence_level,
        reference=output.reference,
        model_name=output.model_name,
        model_version=output.model_version,
        escalated=output.escalated,
        fallback_used=output.fallback_used,
        latency_ms=latency_ms,
    )


def build_ai_usage(
    prediction_id: UUID, user_id: str, motor: str, output: MotorOutput, latency_ms: int
) -> AiUsageRow:
    return AiUsageRow(
        assessment_id=prediction_id,
        user_id=user_id,
        motor=motor,
        model_name=output.model_name,
        input_tokens=output.input_tokens,
        output_tokens=output.output_tokens,
        reasoning_tokens=output.reasoning_tokens,
        image_count=output.image_count,
        is_escalation=output.escalated,
        is_fallback=output.fallback_used,
        cost_usd=output.cost_usd,
        latency_ms=latency_ms,
    )


def build_save_assessment_job(
    prediction_id: UUID,
    request: AssessRequest,
    response: dict,
    motor_outputs: Dict[str, MotorOutput],
    motor_latency_ms: Dict[str, int],
    total_latency_ms: int,
    user_id: str,
) -> SaveAssessmentJob:
    """response = the final answer after apply_fda_rules(); motor_outputs = only the motors that answered.
    user_id = the caller's id from the verified token, never from the request body."""
    context = request.context or RequestContext()  # empty context when the app sent none
    label = pick_label(request)
    reading = build_reading(request, user_id, context)

    assessment = AssessmentRow(
        id=prediction_id,
        user_id=user_id,
        reading_id=reading.id if reading else None,
        input_types=input_types(request),
        input=stored_input(request),
        risk_level=response["risk_level"],
        confidence_score=response["confidence_score"],
        explanation=response["explanation"],
        label=label,
        label_source="system",
        disclaimer_id=response["disclaimer_id"],
        motors_used=response["motors_used"],
        motors_failed=response["motors_failed"],
        total_latency_ms=total_latency_ms,
        app_version=context.app_version,
        state=context.state,
        country=context.country,
    )

    audit_log = AuditLogRow(
        actor_id=user_id,
        actor_role="patient",
        action="create",
        resource_type="assessment",
        resource_id=str(prediction_id),
        patient_id=user_id,
        reason="AI assessment requested",
        after={"risk_level": response["risk_level"], "label": label},
        service="ai",
    )

    return SaveAssessmentJob(
        reading=reading,
        assessment=assessment,
        motor_results=[
            build_motor_result(prediction_id, motor, output, motor_latency_ms[motor])
            for motor, output in motor_outputs.items()
        ],
        ai_usage=[
            build_ai_usage(prediction_id, user_id, motor, output, motor_latency_ms[motor])
            for motor, output in motor_outputs.items()
        ],
        audit_log=audit_log,
    )
