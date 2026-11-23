# ai.assessments: the final AI answer the user saw, one row per /ai/assess call.
# Append-only: a trigger blocks UPDATE, DELETE and TRUNCATE (created in the first migration).

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from collection.base import LABEL_SOURCES, LABELS, RISK_LEVELS, TIMESTAMPTZ, Base, one_of

if TYPE_CHECKING:
    from collection.ai_usage import AiUsage
    from collection.disclaimer import Disclaimer
    from collection.motor_result import MotorResult
    from collection.review import Review
    from collection.vitals_reading import VitalsReading


class Assessment(Base):
    __tablename__ = "assessments"
    __table_args__ = (
        CheckConstraint(one_of("risk_level", RISK_LEVELS), name="assessments_risk_level_check"),
        CheckConstraint("confidence_score BETWEEN 0 AND 1", name="assessments_confidence_score_check"),
        CheckConstraint(one_of("label", LABELS), name="assessments_label_check"),
        CheckConstraint(one_of("label_source", LABEL_SOURCES), name="assessments_label_source_check"),
        CheckConstraint("total_latency_ms >= 0", name="assessments_total_latency_ms_check"),
        {
            "comment": "One row per /ai/assess call: the final answer the user saw. Together with user rows in ai.reviews this is the prediction audit log: date, input, disclaimer shown, user response (Spec, FDA language layer; Proposal Week 3). Append-only.",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID, primary_key=True, server_default=text("gen_random_uuid()"), comment="The prediction_id returned to the app."
    )
    user_id: Mapped[str] = mapped_column(Text)
    reading_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID, ForeignKey("ai.vitals_readings.id", name="assessments_reading_id_fkey")
    )
    input_types: Mapped[List[str]] = mapped_column(ARRAY(Text))
    input: Mapped[dict] = mapped_column(
        JSONB,
        comment="What was sent to the AI (symptoms text, vitals). Never image bytes: images are stored in encrypted S3 and referenced by key.",
    )
    risk_level: Mapped[str] = mapped_column(Text)
    confidence_score: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    explanation: Mapped[str] = mapped_column(
        Text, comment="Exactly as shown, after the FDA language filter (Spec, FDA language layer)."
    )
    label: Mapped[str] = mapped_column(
        Text, comment="Label included in the AI response. Changes are rows in ai.reviews."
    )
    label_source: Mapped[str] = mapped_column(Text, server_default=text("'system'"))
    disclaimer_id: Mapped[str] = mapped_column(
        Text, ForeignKey("ai.disclaimers.id", name="assessments_disclaimer_id_fkey")
    )
    motors_used: Mapped[List[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    motors_failed: Mapped[List[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    total_latency_ms: Mapped[Optional[int]] = mapped_column(
        Integer, comment="For the < 3 s triage target (Spec, Latency targets)."
    )
    app_version: Mapped[Optional[str]] = mapped_column(Text)
    state: Mapped[Optional[str]] = mapped_column(Text)
    country: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())

    reading: Mapped[Optional["VitalsReading"]] = relationship(back_populates="assessments")
    disclaimer: Mapped["Disclaimer"] = relationship(back_populates="assessments")
    motor_results: Mapped[List["MotorResult"]] = relationship(back_populates="assessment")
    ai_usage: Mapped[List["AiUsage"]] = relationship(back_populates="assessment")
    reviews: Mapped[List["Review"]] = relationship(back_populates="assessment")


Index("assessments_user_time_idx", Assessment.user_id, Assessment.created_at.desc())
Index("assessments_reading_idx", Assessment.reading_id)
