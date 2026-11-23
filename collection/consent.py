# ai.consents: what the user agreed to. Withdrawal = a new row with granted = false. Written by Node.
# Append-only: a trigger blocks UPDATE, DELETE and TRUNCATE (created in the first migration).

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, Index, Text, func, text
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column

from collection.base import TIMESTAMPTZ, Base, one_of


class Consent(Base):
    __tablename__ = "consents"
    __table_args__ = (
        CheckConstraint(
            one_of(
                "consent_type",
                (
                    "monitoring_fda",
                    "research_participation",
                    "image_processing",
                    "voice_recording",
                    "parental_consent",
                    "age_13_reconsent",
                    "adult_terms",
                    "triage",
                ),
            ),
            name="consents_consent_type_check",
        ),
        {
            "comment": "Every consent with timestamp, IP, app version and disclaimer version (Proposal §14; Spec, Consent and user rights). Withdrawal = new row with granted = false, stored with its timestamp. Append-only. Written by Node.",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[str] = mapped_column(Text)
    consent_type: Mapped[str] = mapped_column(
        Text,
        comment="monitoring_fda (24/7 monitoring with the FDA disclosure, required), research_participation (optional, revocable), image_processing (photo analysis), voice_recording (Spec, Addendum A), parental_consent (COPPA, under 13), age_13_reconsent (re-consent at 13), adult_terms (adult terms of use), triage (symptom triage).",
    )
    granted: Mapped[bool] = mapped_column(
        Boolean,
        comment="true = given, false = refused or withdrawn. The newest row per user and type is the current state.",
    )
    disclaimer_version: Mapped[str] = mapped_column(Text)
    app_version: Mapped[str] = mapped_column(Text)
    ip_address: Mapped[str] = mapped_column(INET)
    state: Mapped[Optional[str]] = mapped_column(Text)
    country: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())


Index("consents_user_type_time_idx", Consent.user_id, Consent.consent_type, Consent.created_at.desc())
