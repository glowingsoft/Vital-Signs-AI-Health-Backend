# ai.medical_sources: the trusted medical source library (closed list, owned by Vital Signs LLC).
# First filled from db/medical_sources/medical_sources.json (scripts/load_medical_sources.py), then managed only
# through the admin API (app/medical_sources/). The AI may only pick an id from this table; the backend attaches
# the title and link itself.

from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import Boolean, CheckConstraint, Date, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from collection.base import MOTORS, TIMESTAMPTZ, Base, one_of

SOURCE_COUNTRIES = ("US", "MX")
SOURCE_STATUSES = ("draft", "proposed_addition", "active", "inactive", "broken")


class MedicalSource(Base):
    __tablename__ = "medical_sources"
    __table_args__ = (
        CheckConstraint(one_of("country", SOURCE_COUNTRIES), name="medical_sources_country_check"),
        CheckConstraint(one_of("status", SOURCE_STATUSES), name="medical_sources_status_check"),
        CheckConstraint(f"motors <@ ARRAY[{', '.join(repr(m) for m in MOTORS)}]::text[]", name="medical_sources_motors_check"),
        CheckConstraint("link LIKE 'https://%'", name="medical_sources_link_check"),
        CheckConstraint(
            "status <> 'active' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL AND approval_note IS NOT NULL)",
            name="medical_sources_active_needs_approval_check",
        ),
        {
            "comment": "Trusted medical source library (Addendum A, medical-source field). Only status 'active' "
                       "is given to the AI. Never deleted: set status to 'inactive' so past results keep their source.",
        },
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, comment="Stable id the AI returns, e.g. US-AHA-HBP-2025.")
    country: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(Text)
    organisation: Mapped[str] = mapped_column(Text)
    authors: Mapped[Optional[str]] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    year: Mapped[Optional[int]] = mapped_column(Integer)
    publication: Mapped[Optional[str]] = mapped_column(Text)
    doi: Mapped[Optional[str]] = mapped_column(Text)
    link: Mapped[str] = mapped_column(Text)
    topics: Mapped[List[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    motors: Mapped[List[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"), comment="triage, visual, vitals")
    patient_label: Mapped[str] = mapped_column(Text, comment="Short line shown to the patient.")
    evidence_level: Mapped[str] = mapped_column(Text, server_default=text("'clinical_guideline'"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'draft'"))
    client_decision: Mapped[Optional[str]] = mapped_column(Text, comment="Decision in the client's review sheet.")
    needs_client_confirmation: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    approved_by: Mapped[Optional[str]] = mapped_column(Text, comment="Who gave the client's written approval.")
    approved_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMPTZ, comment="Date of the written approval.")
    approval_note: Mapped[Optional[str]] = mapped_column(Text, comment="The approval message, or where it is.")
    current_note: Mapped[Optional[str]] = mapped_column(Text, comment="Whether this is the latest version.")
    notes: Mapped[Optional[str]] = mapped_column(Text)
    link_status: Mapped[Optional[str]] = mapped_column(Text, comment="Result of the last link check: ok or broken.")
    link_checked_at: Mapped[Optional[date]] = mapped_column(Date)
    created_by: Mapped[Optional[str]] = mapped_column(Text, comment="Admin user id, or 'seed' for the first load.")
    updated_by: Mapped[Optional[str]] = mapped_column(Text, comment="Admin user id of the last change.")
    created_by_name: Mapped[Optional[str]] = mapped_column(Text, comment="Name of the admin who added it (from the token).")
    updated_by_name: Mapped[Optional[str]] = mapped_column(
        Text, comment="Name of the admin who changed it last (from the token)."
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())


# GET /ai/admin/medical-sources lists newest first.
Index("medical_sources_created_at_idx", MedicalSource.created_at.desc())
