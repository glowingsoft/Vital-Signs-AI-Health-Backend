# ai.critical_alerts: every full-screen critical alert shown on the phone, written by the Node backend
# (POST /sos/alerts). Append-only: a trigger blocks UPDATE, DELETE and TRUNCATE (created in its migration).

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Numeric, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from collection.base import TIMESTAMPTZ, Base, one_of

ALERT_ACTIONS = ("sos", "call_911", "dismissed", "none")
TRIGGER_SOURCES = ("on_device", "ai_assessment")


class CriticalAlert(Base):
    __tablename__ = "critical_alerts"
    __table_args__ = (
        UniqueConstraint("user_id", "client_alert_id", name="critical_alerts_user_id_client_alert_id_key"),
        CheckConstraint(one_of("action", ALERT_ACTIONS), name="critical_alerts_action_check"),
        CheckConstraint(one_of("trigger_source", TRIGGER_SOURCES), name="critical_alerts_trigger_source_check"),
        CheckConstraint(one_of("device_platform", ("ios", "android")), name="critical_alerts_device_platform_check"),
        CheckConstraint("dismissed_at IS NULL OR dismissed_at >= shown_at", name="critical_alerts_dismissed_at_check"),
        CheckConstraint(
            "time_to_dismiss_seconds IS NULL OR time_to_dismiss_seconds >= 0",
            name="critical_alerts_time_to_dismiss_seconds_check",
        ),
        {
            "comment": "Every critical alert shown on the phone: when, what triggered it, what the user did and the time to dismiss (Spec, Critical alert (SOS)). Written by the Node backend (POST /sos/alerts). Append-only.",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[str] = mapped_column(
        Text, comment="Pseudonymous ID only (Spec, Privacy). Same as ai.vitals_readings.user_id."
    )
    client_alert_id: Mapped[str] = mapped_column(
        Text, comment="ID created on the phone. Unique per user, so an offline alert synced twice is saved once."
    )
    shown_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, comment="When the alert was shown on the phone (Spec, Critical alert (SOS))."
    )
    dismissed_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMPTZ, comment="When the user dismissed it. Empty when the app was closed first."
    )
    time_to_dismiss_seconds: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 2), comment="dismissed_at - shown_at, computed by the Node backend (Spec, Critical alert (SOS))."
    )
    action: Mapped[str] = mapped_column(
        Text, comment="What the user did: sos, call_911, dismissed or none (Spec, Critical alert (SOS))."
    )
    trigger_vital: Mapped[str] = mapped_column(
        Text,
        comment="The vital that raised the alert, named as in ai.vitals_readings: heart_rate, spo2, systolic_bp, diastolic_bp, respiratory_rate.",
    )
    trigger_value: Mapped[str] = mapped_column(Text, comment='The value as shown, e.g. "165" or "185/95".')
    trigger_source: Mapped[str] = mapped_column(
        Text, comment="on_device (phone rules) or ai_assessment (result of POST /ai/assess)."
    )
    assessment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID,
        ForeignKey("ai.assessments.id", name="critical_alerts_assessment_id_fkey"),
        comment="The assessment, when the alert came from POST /ai/assess.",
    )
    recorded_offline: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), comment="True when raised with no internet and synced later."
    )
    supervisor_notified: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    state: Mapped[Optional[str]] = mapped_column(Text)
    country: Mapped[str] = mapped_column(Text)
    app_version: Mapped[Optional[str]] = mapped_column(Text)
    device_platform: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())


Index("critical_alerts_user_time_idx", CriticalAlert.user_id, CriticalAlert.shown_at.desc())
Index("critical_alerts_state_time_idx", CriticalAlert.state, CriticalAlert.shown_at.desc())
