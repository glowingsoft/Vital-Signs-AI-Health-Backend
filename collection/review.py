# ai.reviews: user responses, doctor reviews and label changes. Newest row wins.
# Append-only: a trigger blocks UPDATE, DELETE and TRUNCATE (created in the first migration).

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from collection.base import LABELS, RISK_LEVELS, TIMESTAMPTZ, Base, one_of

if TYPE_CHECKING:
    from collection.assessment import Assessment
    from collection.vitals_reading import VitalsReading


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint(
            one_of("reviewer_role", ("user", "doctor", "labeler", "supervisor")), name="reviews_reviewer_role_check"
        ),
        CheckConstraint(
            one_of("action", ("responded", "confirmed", "corrected", "labeled")), name="reviews_action_check"
        ),
        CheckConstraint(one_of("label", LABELS), name="reviews_label_check"),
        CheckConstraint(one_of("risk_level", RISK_LEVELS), name="reviews_risk_level_check"),
        CheckConstraint("assessment_id IS NOT NULL OR reading_id IS NOT NULL", name="reviews_check"),
        CheckConstraint(
            "action <> 'responded' OR (reviewer_role = 'user' AND user_response IS NOT NULL)", name="reviews_check1"
        ),
        CheckConstraint("action <> 'corrected' OR (label IS NOT NULL OR risk_level IS NOT NULL)", name="reviews_check2"),
        CheckConstraint("action <> 'labeled' OR label IS NOT NULL", name="reviews_check3"),
        {
            "comment": "Every human review of an AI result or reading, newest row wins: user responses (Spec, prediction audit \"user response\"), doctor confirmations and corrections (Spec, Motor 1 and Addendum A), and label changes by labelers and supervisors (Spec, Labeling workflow). Append-only.",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    assessment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID, ForeignKey("ai.assessments.id", name="reviews_assessment_id_fkey")
    )
    reading_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID, ForeignKey("ai.vitals_readings.id", name="reviews_reading_id_fkey")
    )
    reviewer_id: Mapped[str] = mapped_column(Text)
    reviewer_role: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(
        Text,
        comment="responded = user reacted to the result; confirmed = doctor agrees; corrected = new risk level and/or label; labeled = labeler/supervisor sets the label.",
    )
    label: Mapped[Optional[str]] = mapped_column(Text)
    risk_level: Mapped[Optional[str]] = mapped_column(Text)
    user_response: Mapped[Optional[str]] = mapped_column(
        Text, comment="e.g. acknowledged, contacted_doctor, dismissed, disagreed."
    )
    note: Mapped[Optional[str]] = mapped_column(Text)
    is_final: Mapped[bool] = mapped_column(
        Boolean,
        server_default=text("false"),
        comment="True = state clinical supervisor's final decision (Spec, Labeling workflow).",
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())

    assessment: Mapped[Optional["Assessment"]] = relationship(back_populates="reviews")
    reading: Mapped[Optional["VitalsReading"]] = relationship(back_populates="reviews")


Index("reviews_assessment_idx", Review.assessment_id, Review.created_at.desc())
Index("reviews_reading_idx", Review.reading_id, Review.created_at.desc())
