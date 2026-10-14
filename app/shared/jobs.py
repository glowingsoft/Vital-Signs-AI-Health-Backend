# Pydantic models for the jobs put on the Redis queue. Each *Row model has the same fields as
# one table in the "ai" schema (see db/sql/02_schema_and_tables.sql), so the worker can insert it as-is.

from datetime import datetime, timezone
from typing import List, Literal, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

RiskLevel = Literal["low", "medium", "high", "critical"]
Label = Literal["confirmed_diagnosis", "reported_symptom", "normal"]
LabelSource = Literal["user", "labeler", "supervisor", "doctor", "system"]
Motor = Literal["triage", "visual", "vitals"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# Ids and timestamps are filled in when the job is BUILT (default_factory runs once, in the API).
# So if the worker retries a job, it inserts the same ids again and ON CONFLICT skips them.
# created_at is the time of the request, not the time the worker got to it.


class ReadingRow(BaseModel):  # ai.vitals_readings
    id: UUID = Field(default_factory=uuid4)
    user_id: str
    client_reading_id: str
    measured_at: datetime
    received_at: datetime = Field(default_factory=utc_now)
    heart_rate: Optional[int] = None
    systolic_bp: Optional[int] = None
    diastolic_bp: Optional[int] = None
    respiratory_rate: Optional[int] = None
    spo2: Optional[float] = None
    hrv_ms: Optional[float] = None
    sleep_minutes: Optional[int] = None
    label: Label = "normal"
    label_source: LabelSource = "user"
    model_version: Optional[str] = None
    source: Literal["apple_health", "samsung_health", "device", "manual"]
    recorded_offline: bool = False
    device_platform: Optional[Literal["ios", "android"]] = None
    device_model: Optional[str] = None
    os_version: Optional[str] = None
    app_version: Optional[str] = None
    state: Optional[str] = None
    country: str
    created_at: datetime = Field(default_factory=utc_now)


class AssessmentRow(BaseModel):  # ai.assessments (append-only)
    id: UUID  # the prediction_id returned to the app
    user_id: str
    reading_id: Optional[UUID] = None
    input_types: List[str]
    input: dict  # saved in a jsonb column
    risk_level: RiskLevel
    confidence_score: float
    explanation: str  # after the FDA filter, exactly as the user saw it
    label: Label
    label_source: LabelSource = "system"
    disclaimer_id: str
    motors_used: List[str] = []
    motors_failed: List[str] = []
    total_latency_ms: Optional[int] = None
    app_version: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)


class MotorResultRow(BaseModel):  # ai.motor_results
    id: UUID = Field(default_factory=uuid4)
    assessment_id: UUID
    motor: Motor
    risk_level: RiskLevel
    confidence_score: float
    explanation: str  # raw motor text, before the FDA filter
    finding_type: Optional[str] = None
    evidence_level: Optional[str] = None
    reference: Optional[str] = None
    model_name: str
    model_version: Optional[str] = None
    escalated: bool = False
    fallback_used: bool = False
    latency_ms: Optional[int] = None
    created_at: datetime = Field(default_factory=utc_now)


class AiUsageRow(BaseModel):  # ai.ai_usage
    id: UUID = Field(default_factory=uuid4)
    assessment_id: Optional[UUID] = None
    user_id: str
    motor: Motor
    model_name: str
    aws_region: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    image_count: int = 0
    is_escalation: bool = False
    is_fallback: bool = False
    is_retry: bool = False
    cost_usd: float = 0
    latency_ms: Optional[int] = None
    created_at: datetime = Field(default_factory=utc_now)


class AuditLogRow(BaseModel):  # ai.audit_log (append-only)
    id: UUID = Field(default_factory=uuid4)
    actor_id: str
    actor_role: Literal["patient", "doctor", "supervisor", "labeler", "admin", "developer", "system"]
    action: Literal["view", "create", "update", "export", "delete"]
    resource_type: str
    resource_id: Optional[str] = None
    patient_id: Optional[str] = None
    reason: str = Field(min_length=1)  # the database refuses an empty reason
    before: Optional[dict] = None  # saved in a jsonb column
    after: Optional[dict] = None  # saved in a jsonb column
    service: Literal["ai", "node"] = "ai"
    ip_address: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)


# ---------- The jobs themselves ----------


class SaveAssessmentJob(BaseModel):
    """Everything about one /ai/assess call. The worker saves it all in ONE transaction."""

    job_id: UUID = Field(default_factory=uuid4)
    reading: Optional[ReadingRow] = None  # only when vitals were sent
    assessment: AssessmentRow
    motor_results: List[MotorResultRow] = []
    ai_usage: List[AiUsageRow] = []
    audit_log: AuditLogRow


class AuditJob(BaseModel):
    """One standalone audit_log row, e.g. "a doctor viewed this patient's data"."""

    job_id: UUID = Field(default_factory=uuid4)
    audit_log: AuditLogRow
