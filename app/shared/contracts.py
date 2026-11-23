# The data shapes every part of the app agrees on: what goes into a motor, what comes out,
# and what the app sends to / gets back from the orchestrator.

from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, model_validator

# Literal = the value must be one of these strings (like Django "choices").
RiskLevel = Literal["low", "medium", "high", "critical"]

# Lowest to highest. The position in this list is how we compare two risk levels.
RISK_ORDER = ["low", "medium", "high", "critical"]

# Version of the disclaimer text shown to the user (the text itself lives in fda_filter.py).
DISCLAIMER_ID = "disc_v1"


def highest_risk(levels: List[str]) -> str:
    """Return the most serious risk level in the list, e.g. ["low", "high"] -> "high"."""
    return max(levels, key=RISK_ORDER.index)


# ---------- Motor contract (used by every motor) ----------

# Pydantic model = like a DRF serializer: it validates incoming JSON and documents the shape in /docs.
class MotorInput(BaseModel):
    user_id: str  # pseudonymous id, never a real name or email
    timestamp: datetime
    input_type: Literal["text", "image", "vitals"]
    data: dict  # motor-specific content, e.g. {"message": "..."} for triage


class MotorOutput(BaseModel):
    risk_level: RiskLevel
    confidence_score: float = Field(ge=0, le=1)  # ge/le = validation, like MinValueValidator/MaxValueValidator
    explanation: str
    disclaimer_id: str
    finding_type: Optional[str] = None  # "= None" makes the field optional, like null=True, blank=True
    evidence_level: Optional[str] = None
    reference: Optional[str] = None
    # Which model answered and what it cost. Saved to ai.motor_results and ai.ai_usage.
    model_name: str = "stub"
    model_version: Optional[str] = None
    escalated: bool = False  # True when a stronger model was used after the default one
    fallback_used: bool = False  # True when a backup model answered
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    image_count: int = 0
    cost_usd: float = 0


# ---------- Orchestrator contract (used by the app) ----------

class Vitals(BaseModel):
    # The ge/le limits match the CHECK constraints on ai.vitals_readings. Bad values are rejected
    # here with a 422, because a value the database refuses would make the save job fail later.
    heart_rate: Optional[int] = Field(None, ge=0, le=300)  # beats per minute
    spo2: Optional[int] = Field(None, ge=0, le=100)  # blood oxygen, percent
    systolic: Optional[int] = Field(None, ge=0, le=300)  # blood pressure, top number
    diastolic: Optional[int] = Field(None, ge=0, le=250)  # blood pressure, bottom number
    respiratory_rate: Optional[int] = Field(None, ge=0, le=100)  # breaths per minute
    client_reading_id: str  # id made on the phone; the same reading sent twice is saved once
    measured_at: Optional[AwareDatetime] = None  # must include a timezone; empty = now
    source: Literal["apple_health", "samsung_health", "device", "manual"] = "manual"
    recorded_offline: bool = False
    model_version: Optional[str] = None  # version of the on-device vitals model

    # model_validator runs after the single fields are checked, to check them together.
    @model_validator(mode="after")
    def at_least_one_reading(self):
        readings = [self.heart_rate, self.spo2, self.systolic, self.diastolic, self.respiratory_rate]
        if all(value is None for value in readings):
            raise ValueError("vitals must contain at least one reading")
        return self


class RequestContext(BaseModel):
    """Where the request came from. Stored with the reading and the assessment."""

    app_version: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    device_platform: Optional[Literal["ios", "android"]] = None
    device_model: Optional[str] = None
    os_version: Optional[str] = None


class AssessRequest(BaseModel):
    # No user_id here: it always comes from the verified token (see orchestrator/routes.py).
    # Unknown fields are ignored, so a user_id sent in the body has no effect.
    symptoms: Optional[str] = None
    image: Optional[str] = None  # for now any string (e.g. an image reference); real images come later
    vitals: Optional[Vitals] = None
    context: Optional[RequestContext] = None

    @model_validator(mode="after")
    def country_required_with_vitals(self):
        # ai.vitals_readings.country is NOT NULL, so a reading cannot be saved without it.
        if self.vitals and not (self.context and self.context.country):
            raise ValueError("context.country is required when vitals are sent")
        return self


class AssessResponse(BaseModel):
    risk_level: RiskLevel
    confidence_score: float
    explanation: str
    disclaimer_id: str
    disclaimer: str
    motors_used: List[str]
    motors_failed: List[str]
    prediction_id: UUID  # id of the saved assessment; the app uses it to refer to this result later
