# The data shapes every part of the app agrees on: what goes into a motor, what comes out,
# and what the app sends to / gets back from the orchestrator.

import re
from urllib.parse import unquote
from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator

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
    # Patient's country from the request context (US or MX): which trusted medical sources the motor may cite.
    country: Optional[str] = None


class MotorOutput(BaseModel):
    risk_level: RiskLevel
    confidence_score: float = Field(ge=0, le=1)  # ge/le = validation, like MinValueValidator/MaxValueValidator
    explanation: str
    disclaimer_id: str
    finding_type: Optional[str] = None  # "= None" makes the field optional, like null=True, blank=True
    evidence_level: Optional[str] = None
    reference: Optional[str] = None  # full citation from the trusted library, attached by the backend
    # Result of the source check (app/medical_sources/library.check_source). Saved to ai.motor_results.
    source_id: Optional[str] = None  # ai.medical_sources.id, only when the pick matched an allowed source
    source_status: Optional[Literal["matched", "pending_medical_review"]] = None  # None = nothing was assessed
    source_label: Optional[str] = None  # short line for the patient, e.g. "according to CDC guidance"
    source_link: Optional[str] = None  # the library's verified link
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


S3_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-/]{0,499}")
# https://<bucket>.s3.<region>.amazonaws.com/<key> (Node's IB_URL + key), query string ignored.
BUCKET_URL = re.compile(r"^https://[a-z0-9.-]+\.s3[a-z0-9.-]*\.amazonaws\.com/([^?#]+)(?:[?#].*)?$")


class AssessRequest(BaseModel):
    # No user_id here: it always comes from the verified token (see orchestrator/routes.py).
    # Unknown fields are ignored, so a user_id sent in the body has no effect.
    symptoms: Optional[str] = None
    image: Optional[str] = None  # the photo (base64), only for the analysis: never saved
    # S3 keys of the files the app uploaded first (Node POST /upload), saved as they are in ai.assessments.input
    # ("image" / "audio"), so Node can show them later as short-lived private links (GET /assessments).
    image_key: Optional[str] = None  # the photo sent in "image"
    audio_key: Optional[str] = None  # the voice recording the symptoms text came from
    # The voice note in Node (MongoDB "voices" _id) the symptoms came from, when the app has no audio_key:
    # saved as it is, and Node finds the recording's S3 key through it (GET /assessments).
    voice_id: Optional[str] = Field(None, pattern=r"^[0-9a-f]{24}$")
    vitals: Optional[Vitals] = None
    context: Optional[RequestContext] = None

    @field_validator("image_key", "audio_key")
    @classmethod
    def plain_s3_key(cls, value: Optional[str]) -> Optional[str]:
        # Only a plain key such as "1764140000000-a1b2c3d4e5f6.jpg" or "uploads/x.m4a":
        # no URL, no leading "/", no "..", so it can only ever name an object in the app's own bucket.
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        # The app may send the full link it got from Node (IB_URL + key): keep only the key.
        bucket_url = BUCKET_URL.match(value)
        if bucket_url:
            value = unquote(bucket_url.group(1))
        if not S3_KEY.fullmatch(value) or ".." in value:
            raise ValueError("must be the S3 key returned by the upload, e.g. 1764140000000-a1b2c3d4e5f6.jpg")
        return value

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
