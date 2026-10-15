# ai.ai_usage: cost of every AI call (metering by user, model and motor for the USD 0.50 target).

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from collection.base import MOTORS, TIMESTAMPTZ, Base, one_of

if TYPE_CHECKING:
    from collection.assessment import Assessment


class AiUsage(Base):
    __tablename__ = "ai_usage"
    __table_args__ = (
        CheckConstraint(one_of("motor", MOTORS), name="ai_usage_motor_check"),
        CheckConstraint("input_tokens >= 0", name="ai_usage_input_tokens_check"),
        CheckConstraint("output_tokens >= 0", name="ai_usage_output_tokens_check"),
        CheckConstraint("reasoning_tokens >= 0", name="ai_usage_reasoning_tokens_check"),
        CheckConstraint("image_count >= 0", name="ai_usage_image_count_check"),
        CheckConstraint("cost_usd >= 0", name="ai_usage_cost_usd_check"),
        CheckConstraint("latency_ms >= 0", name="ai_usage_latency_ms_check"),
        {
            "comment": "One row per billable AI call: cost metering by user, model and motor for the USD 0.50 target (Proposal M1 and AI cost section).",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    assessment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID, ForeignKey("ai.assessments.id", name="ai_usage_assessment_id_fkey")
    )
    user_id: Mapped[str] = mapped_column(Text)
    motor: Mapped[str] = mapped_column(Text)
    model_name: Mapped[str] = mapped_column(Text)
    aws_region: Mapped[Optional[str]] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    reasoning_tokens: Mapped[int] = mapped_column(
        Integer,
        server_default=text("0"),
        comment="Billable reasoning tokens count toward the target (Proposal, How the target is measured).",
    )
    image_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    is_escalation: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    is_fallback: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    is_retry: Mapped[bool] = mapped_column(
        Boolean,
        server_default=text("false"),
        comment="Retries and failovers count toward the target (Proposal, How the target is measured).",
    )
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), server_default=text("0"))
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())

    assessment: Mapped[Optional["Assessment"]] = relationship(back_populates="ai_usage")


Index("ai_usage_user_time_idx", AiUsage.user_id, AiUsage.created_at)
Index("ai_usage_time_idx", AiUsage.created_at)
Index("ai_usage_assessment_idx", AiUsage.assessment_id)
