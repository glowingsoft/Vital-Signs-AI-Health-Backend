# ai.audit_log: who viewed or changed health data, when and why, with before/after values.
# Append-only: a trigger blocks UPDATE, DELETE and TRUNCATE (created in the first migration).

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, Index, Text, func, text
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from collection.base import TIMESTAMPTZ, Base, one_of


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint(
            one_of("actor_role", ("patient", "doctor", "supervisor", "labeler", "admin", "developer", "system")),
            name="audit_log_actor_role_check",
        ),
        CheckConstraint(
            one_of("action", ("view", "create", "update", "export", "delete")), name="audit_log_action_check"
        ),
        CheckConstraint("length(trim(reason)) > 0", name="audit_log_reason_check"),
        CheckConstraint(one_of("service", ("ai", "node")), name="audit_log_service_check"),
        {
            "comment": "Who viewed or changed health data, when and why (Proposal M1 §13; Spec, Privacy), and the clinical audit trail with before/after values, e.g. a doctor changing the label or risk of an AI response (Spec, Addendum A). Append-only.",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    actor_id: Mapped[str] = mapped_column(Text)
    actor_role: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    resource_type: Mapped[str] = mapped_column(Text)
    resource_id: Mapped[Optional[str]] = mapped_column(Text)
    patient_id: Mapped[Optional[str]] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(
        Text, comment="Why the data was accessed or changed. Required (Spec, Mandatory controls)."
    )
    before: Mapped[Optional[dict]] = mapped_column(
        JSONB, comment='Values before the change, e.g. {"label": "reported_symptom", "risk_level": "medium"}.'
    )
    after: Mapped[Optional[dict]] = mapped_column(
        JSONB, comment='Values after the change, e.g. {"label": "confirmed_diagnosis", "risk_level": "high"}.'
    )
    service: Mapped[str] = mapped_column(Text)
    ip_address: Mapped[Optional[str]] = mapped_column(INET)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())


Index("audit_log_patient_time_idx", AuditLog.patient_id, AuditLog.created_at.desc())
Index("audit_log_actor_time_idx", AuditLog.actor_id, AuditLog.created_at.desc())
Index("audit_log_resource_idx", AuditLog.resource_type, AuditLog.resource_id)
