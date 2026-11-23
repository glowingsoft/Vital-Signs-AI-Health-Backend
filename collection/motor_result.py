# ai.motor_results: each motor's own answer inside one assessment, before consolidation.

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from collection.base import MOTORS, RISK_LEVELS, SOURCE_MATCH_STATUSES, TIMESTAMPTZ, Base, one_of

if TYPE_CHECKING:
    from collection.assessment import Assessment


class MotorResult(Base):
    __tablename__ = "motor_results"
    __table_args__ = (
        CheckConstraint(one_of("motor", MOTORS), name="motor_results_motor_check"),
        CheckConstraint(one_of("risk_level", RISK_LEVELS), name="motor_results_risk_level_check"),
        CheckConstraint("confidence_score BETWEEN 0 AND 1", name="motor_results_confidence_score_check"),
        CheckConstraint("latency_ms >= 0", name="motor_results_latency_ms_check"),
        CheckConstraint(one_of("source_status", SOURCE_MATCH_STATUSES), name="motor_results_source_status_check"),
        {
            "comment": "Each motor's own answer inside one assessment, before consolidation.",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("ai.assessments.id", name="motor_results_assessment_id_fkey")
    )
    motor: Mapped[str] = mapped_column(Text)
    risk_level: Mapped[str] = mapped_column(Text)
    confidence_score: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    explanation: Mapped[str] = mapped_column(Text, comment="Raw motor text, before the FDA filter. Kept for audit.")
    finding_type: Mapped[Optional[str]] = mapped_column(Text, comment="Medical-source field (Spec, Addendum A).")
    evidence_level: Mapped[Optional[str]] = mapped_column(Text, comment="Medical-source field (Spec, Addendum A).")
    reference: Mapped[Optional[str]] = mapped_column(
        Text, comment="Traceable reference from the curated library (Spec, Addendum A)."
    )
    # No foreign key on purpose: a result must still be saved if the library row is missing.
    source_id: Mapped[Optional[str]] = mapped_column(
        Text, comment="ai.medical_sources.id the AI picked and the backend confirmed. Empty when no source matched."
    )
    source_status: Mapped[Optional[str]] = mapped_column(
        Text, comment="matched, or pending_medical_review when the AI gave no source or one not in the library."
    )
    model_name: Mapped[str] = mapped_column(Text, comment='Model stored per response (Spec, Motor 1). "stub" during M1.')
    model_version: Mapped[Optional[str]] = mapped_column(Text)
    escalated: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), comment="True when Sonnet was used after Haiku (Proposal, Routing strategy)."
    )
    fallback_used: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), comment="True when a backup model was used (Proposal, Routing strategy)."
    )
    latency_ms: Mapped[Optional[int]] = mapped_column(
        Integer, comment="For the per-model p95 latency benchmark (Spec, Latency targets)."
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())

    assessment: Mapped["Assessment"] = relationship(back_populates="motor_results")


Index("motor_results_assessment_idx", MotorResult.assessment_id)
