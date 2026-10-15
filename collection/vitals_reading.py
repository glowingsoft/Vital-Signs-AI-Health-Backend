# ai.vitals_readings: the health numbers from the phone, with their first label.

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, CheckConstraint, Index, Integer, Numeric, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from collection.base import LABEL_SOURCES, LABELS, TIMESTAMPTZ, Base, one_of

if TYPE_CHECKING:
    from collection.assessment import Assessment
    from collection.review import Review


class VitalsReading(Base):
    __tablename__ = "vitals_readings"
    # CHECK constraints live here, not on the columns, so Alembic autogenerate writes them into migrations.
    __table_args__ = (
        UniqueConstraint("user_id", "client_reading_id", name="vitals_readings_user_id_client_reading_id_key"),
        CheckConstraint("heart_rate BETWEEN 0 AND 300", name="vitals_readings_heart_rate_check"),
        CheckConstraint("systolic_bp BETWEEN 0 AND 300", name="vitals_readings_systolic_bp_check"),
        CheckConstraint("diastolic_bp BETWEEN 0 AND 250", name="vitals_readings_diastolic_bp_check"),
        CheckConstraint("respiratory_rate BETWEEN 0 AND 100", name="vitals_readings_respiratory_rate_check"),
        CheckConstraint("spo2 BETWEEN 0 AND 100", name="vitals_readings_spo2_check"),
        CheckConstraint("hrv_ms >= 0", name="vitals_readings_hrv_ms_check"),
        CheckConstraint("sleep_minutes >= 0", name="vitals_readings_sleep_minutes_check"),
        CheckConstraint(one_of("label", LABELS), name="vitals_readings_label_check"),
        CheckConstraint(one_of("label_source", LABEL_SOURCES), name="vitals_readings_label_source_check"),
        CheckConstraint(
            one_of("source", ("apple_health", "samsung_health", "device", "manual")),
            name="vitals_readings_source_check",
        ),
        CheckConstraint(one_of("device_platform", ("ios", "android")), name="vitals_readings_device_platform_check"),
        # At least one measurement must be present.
        CheckConstraint(
            "num_nonnulls(heart_rate, systolic_bp, diastolic_bp, respiratory_rate, spo2, hrv_ms, sleep_minutes) > 0",
            name="vitals_readings_check",
        ),
        {
            "comment": "Every vitals reading with its label, label source, model version, state, country and device info (Spec, Record schema; Proposal M1).",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[str] = mapped_column(Text, comment="Pseudonymous ID only (Spec, Privacy).")
    client_reading_id: Mapped[str] = mapped_column(
        Text,
        comment="ID created on the phone. Unique per user, so offline sync never saves a reading twice (Spec, Motor 3 offline).",
    )
    measured_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    received_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())
    heart_rate: Mapped[Optional[int]] = mapped_column(Integer)
    systolic_bp: Mapped[Optional[int]] = mapped_column(Integer)
    diastolic_bp: Mapped[Optional[int]] = mapped_column(Integer)
    respiratory_rate: Mapped[Optional[int]] = mapped_column(Integer)
    spo2: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    hrv_ms: Mapped[Optional[Decimal]] = mapped_column(Numeric(7, 2))
    sleep_minutes: Mapped[Optional[int]] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(
        Text,
        server_default=text("'normal'"),
        comment="First label when the reading arrives. Later labels are rows in ai.reviews (Spec, The three mandatory labels).",
    )
    label_source: Mapped[str] = mapped_column(
        Text, server_default=text("'user'"), comment="Who set the first label (Spec, Record schema)."
    )
    model_version: Mapped[Optional[str]] = mapped_column(
        Text, comment="Version of the on-device vitals model that analysed it (Spec, Record schema)."
    )
    source: Mapped[str] = mapped_column(Text)
    recorded_offline: Mapped[bool] = mapped_column(
        Boolean,
        server_default=text("false"),
        comment="True when captured with no internet and synced later (Proposal Week 2).",
    )
    device_platform: Mapped[Optional[str]] = mapped_column(Text)
    device_model: Mapped[Optional[str]] = mapped_column(Text)
    os_version: Mapped[Optional[str]] = mapped_column(Text)
    app_version: Mapped[Optional[str]] = mapped_column(Text)
    state: Mapped[Optional[str]] = mapped_column(Text)
    country: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())

    assessments: Mapped[List["Assessment"]] = relationship(back_populates="reading")
    reviews: Mapped[List["Review"]] = relationship(back_populates="reading")


Index("vitals_readings_user_time_idx", VitalsReading.user_id, VitalsReading.measured_at.desc())
