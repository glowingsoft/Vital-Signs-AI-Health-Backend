# The data shapes every part of the app agrees on: what goes into a motor, what comes out,
# and what the app sends to / gets back from the orchestrator.

import re
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
    # Patient's country from the request context (US or MX): picks which trusted medical sources the AI may cite.
    country: Optional[str] = None


class MotorOutput(BaseModel):
    risk_level: RiskLevel
    confidence_score: float = Field(ge=0, le=1)  # ge/le = validation, like MinValueValidator/MaxValueValidator
    explanation: str
    disclaimer_id: str
    finding_type: Optional[str] = None  # "= None" makes the field optional, like null=True, blank=True
    evidence_level: Optional[str] = None
    reference: Optional[str] = None  # full citation from the trusted library, attached by the backend
    # Result of the check after the AI answer (app/medical_sources/library.check_source).
    source_id: Optional[str] = None  # library id, only when it matched
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


# S3 key of the photo the app uploaded, e.g. "1731473000000-a1b2c3d4e5f6.png" or "scans/1234455.png".
# Letters, digits, ".", "_", "-" and "/" only, no "..", ending in an image extension.
IMAGE_KEY_PATTERN = re.compile(r"^(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9._/-]{0,250}\.(jpe?g|png|gif|webp|heic)$", re.IGNORECASE)

# S3 key of the triage voice recording the app uploaded, e.g. "1731473000000-a1b2c3d4e5f6.m4a".
# Same rules as IMAGE_KEY_PATTERN, ending in an audio extension.
AUDIO_KEY_PATTERN = re.compile(
    r"^(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9._/-]{0,250}\.(m4a|mp3|wav|aac|caf|ogg|opus|webm|3gp|amr)$", re.IGNORECASE
)


class AssessRequest(BaseModel):
    # No user_id here: it always comes from the verified token (see orchestrator/routes.py).
    # Unknown fields are ignored, so a user_id sent in the body has no effect.
    symptoms: Optional[str] = None
<<<<<<< Updated upstream
    image: Optional[str] = None  # for now any string (e.g. an image reference); real images come later
    vitals: Optional[Vitals] = None
    context: Optional[RequestContext] = None

=======
    image: Optional[str] = None  # base64 JPEG, PNG, GIF or WEBP, at most config.MAX_IMAGE_BYTES once decoded
    # S3 key of the same photo, uploaded by the app. Saved in ai.assessments.input["image"] in place of the
    # image bytes, so a doctor can open the photo from the assessment (Node signs a short-lived link).
    image_key: Optional[str] = None
    # S3 key of the voice recording of the symptoms, uploaded by the app (Addendum A: capture and labeling only).
    # The app turns the speech into text on the phone and sends it in "symptoms"; the audio is never sent to a
    # model. Saved in ai.assessments.input["audio"] so a doctor can play it from the assessment.
    audio_key: Optional[str] = None
    vitals: Optional[Vitals] = None
    context: Optional[RequestContext] = None

    @field_validator("image")
    @classmethod
    def image_is_readable(cls, image: Optional[str]) -> Optional[str]:
        # A bad image is refused with a 422 before any motor is called (InvalidImage is a ValueError).
        if image is not None:
            decode_image(image)
        return image

    @field_validator("image_key")
    @classmethod
    def image_key_is_safe(cls, image_key: Optional[str]) -> Optional[str]:
        if image_key is None:
            return None
        image_key = image_key.strip()
        if not IMAGE_KEY_PATTERN.match(image_key):
            raise ValueError("image_key must be an S3 key such as '1234455.png' (jpg, jpeg, png, gif, webp or heic)")
        return image_key

    @field_validator("audio_key")
    @classmethod
    def audio_key_is_safe(cls, audio_key: Optional[str]) -> Optional[str]:
        if audio_key is None:
            return None
        audio_key = audio_key.strip()
        if not AUDIO_KEY_PATTERN.match(audio_key):
            raise ValueError(
                "audio_key must be an S3 key such as '1234455.m4a' (m4a, mp3, wav, aac, caf, ogg, opus, webm, 3gp or amr)"
            )
        return audio_key

    @model_validator(mode="after")
    def audio_key_needs_symptoms(self):
        # The recording is the spoken symptoms; triage reads their text, so the text must come with it.
        if self.audio_key and not self.symptoms:
            raise ValueError("audio_key is sent together with symptoms (the text of the recording)")
        return self

    @model_validator(mode="after")
    def image_key_needs_image(self):
        # The key only says where the photo is stored; the AI still needs the photo itself in "image".
        if self.image_key and not self.image:
            raise ValueError("image_key is sent together with image")
        return self

>>>>>>> Stashed changes
    @model_validator(mode="after")
    def country_required_with_vitals(self):
        # ai.vitals_readings.country is NOT NULL, so a reading cannot be saved without it.
        if self.vitals and not (self.context and self.context.country):
            raise ValueError("context.country is required when vitals are sent")
        return self


class SourceForApp(BaseModel):
    """The medical source of one motor's answer, as shown to the patient. Only the short label and the
    library's verified link: the full citation, source_id, finding_type and evidence_level are for doctors."""

    motor: Literal["triage", "visual", "vitals"]
    status: Literal["matched", "pending_medical_review"]
    label: str  # library patient_label when matched, else "Pending medical review" in the patient's language
    link: Optional[str] = None  # library link when matched, else null


class AssessResponse(BaseModel):
    risk_level: RiskLevel
    confidence_score: float
    explanation: str
    disclaimer_id: str
    disclaimer: str
    motors_used: List[str]
    motors_failed: List[str]
    sources: List[SourceForApp] = []  # one per motor in motors_used, same order
    prediction_id: UUID  # id of the saved assessment; the app uses it to refer to this result later
