# Request and response bodies of the admin API (controller.py). Pydantic checks the shape and the allowed
# values; the rules that need the database (id already used, status changes) are in service.py.

from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.medical_sources.constants import EVIDENCE_LEVELS, ID_PATTERN, SOURCE_MOTORS, TOPICS

Country = Literal["US", "MX"]
Language = Literal["en", "es"]
Status = Literal["draft", "proposed_addition", "active", "inactive", "broken"]
EvidenceLevel = Literal["clinical_guideline", "peer_reviewed_study", "expert_consensus", "public_health_guidance"]
Motor = Literal["triage", "visual", "vitals"]
ClientDecision = Literal["approve", "approve_with_changes", "reject"]

assert set(EvidenceLevel.__args__) == set(EVIDENCE_LEVELS) and set(Motor.__args__) == set(SOURCE_MOTORS)


def check_link(link: Optional[str]) -> Optional[str]:
    if link is None:
        return None
    link = link.strip()
    if not link.startswith("https://") or " " in link or len(link) > 1000:
        raise ValueError("link must be a full https:// address")
    return link


def check_topics(topics: Optional[List[str]]) -> Optional[List[str]]:
    if topics is None:
        return None
    unknown = sorted(set(topics) - set(TOPICS))
    if unknown:
        raise ValueError(f"unknown topics {unknown}; allowed: {sorted(TOPICS)}")
    return list(dict.fromkeys(topics))  # no duplicates, order kept


def no_duplicates(values: Optional[list]) -> Optional[list]:
    return None if values is None else list(dict.fromkeys(values))


class SourceFields(BaseModel):
    """The fields an admin can set. extra="forbid": a misspelled field is a 422, not silently ignored."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    language: Optional[Language] = None
    organisation: Optional[str] = Field(None, min_length=2, max_length=500)
    authors: Optional[str] = Field(None, max_length=500)
    title: Optional[str] = Field(None, min_length=3, max_length=1000)
    year: Optional[int] = Field(None, ge=1900, le=2100)
    publication: Optional[str] = Field(None, max_length=1000)
    doi: Optional[str] = Field(None, max_length=200)
    link: Optional[str] = None
    topics: Optional[List[str]] = Field(None, min_length=1)
    motors: Optional[List[Motor]] = Field(None, min_length=1)
    patient_label: Optional[str] = Field(None, min_length=3, max_length=300)
    evidence_level: Optional[EvidenceLevel] = None
    client_decision: Optional[ClientDecision] = None
    needs_client_confirmation: Optional[bool] = None
    current_note: Optional[str] = Field(None, max_length=1000)
    notes: Optional[str] = Field(None, max_length=2000)

    @field_validator("link")
    @classmethod
    def link_is_https(cls, value: Optional[str]) -> Optional[str]:
        return check_link(value)

    @field_validator("topics")
    @classmethod
    def topics_are_known(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        return check_topics(value)

    @field_validator("motors")
    @classmethod
    def motors_once(cls, value: Optional[list]) -> Optional[list]:
        return no_duplicates(value)


class SourceCreate(SourceFields):
    """POST /ai/admin/medical-sources. The new source is saved as "draft": the AI cannot use it until approved."""

    id: str = Field(description="e.g. US-AHA-HF-2022: the country, then capital letters, digits and dashes")
    country: Country
    language: Language
    organisation: str = Field(min_length=2, max_length=500)
    title: str = Field(min_length=3, max_length=1000)
    link: str
    topics: List[str] = Field(min_length=1)
    motors: List[Motor] = Field(min_length=1)
    patient_label: str = Field(min_length=3, max_length=300)
    evidence_level: EvidenceLevel = "clinical_guideline"
    reason: Optional[str] = Field(None, max_length=1000, description="Why it is added; saved in the audit log")

    @field_validator("id")
    @classmethod
    def id_format(cls, value: str) -> str:
        value = value.strip()
        if not ID_PATTERN.match(value):
            raise ValueError("id must look like US-AHA-HBP-2025 or MX-OPS-BEC (country, then A-Z, 0-9 and dashes)")
        return value

    @model_validator(mode="after")
    def id_matches_country(self):
        if not self.id.startswith(self.country + "-"):
            raise ValueError(f"id must start with '{self.country}-' for country {self.country}")
        return self


class ClientApproval(BaseModel):
    """The client's written approval (a written message from Jose is enough). Saved on the source."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    approved_by: str = Field(min_length=2, max_length=200, description="e.g. Jose (Vital Signs LLC)")
    approval_note: str = Field(min_length=5, max_length=2000, description="The approval message, or where it is (email date, subject)")
    approved_at: Optional[date] = Field(None, description="Date of the written approval; empty = today")


class SourceUpdate(SourceFields):
    """PATCH /ai/admin/medical-sources/{id}: only the fields sent are changed.
    Changing an active source needs the client's approval of the change in "approval"."""

    approval: Optional[ClientApproval] = None
    reason: Optional[str] = Field(None, max_length=1000, description="Why it is changed; saved in the audit log")


class ApproveRequest(ClientApproval):
    """POST /ai/admin/medical-sources/{id}/approve: the source becomes active and the AI may cite it."""


class DeactivateRequest(BaseModel):
    """POST /ai/admin/medical-sources/{id}/deactivate: the AI stops citing it. Sources are never deleted."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=3, max_length=1000)


class SourceOut(BaseModel):
    id: str
    country: Country
    language: str
    organisation: str
    authors: Optional[str] = None
    title: str
    year: Optional[int] = None
    publication: Optional[str] = None
    doi: Optional[str] = None
    link: str
    topics: List[str]
    motors: List[str]
    patient_label: str
    evidence_level: str
    status: Status
    client_decision: Optional[str] = None
    needs_client_confirmation: bool
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    approval_note: Optional[str] = None
    current_note: Optional[str] = None
    notes: Optional[str] = None
    link_status: Optional[str] = None
    link_checked_at: Optional[date] = None
    created_by: Optional[str] = Field(None, description="Name of the admin who added it ('System (seed)' for the first load)")
    updated_by: Optional[str] = Field(None, description="Name of the admin who changed it last")
    created_by_id: Optional[str] = Field(None, description="User id of created_by (for the audit trail)")
    updated_by_id: Optional[str] = Field(None, description="User id of updated_by (for the audit trail)")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


SortField = Literal["created_at", "updated_at", "id", "title", "organisation", "country", "status", "year"]
SortOrder = Literal["asc", "desc"]


class SourceList(BaseModel):
    count: int = Field(description="How many sources match the filters (all pages)")
    sources: List[SourceOut]
    page: Optional[int] = None  # only with ?limit=
    limit: Optional[int] = None
    total_pages: Optional[int] = None


class TopicsOut(BaseModel):
    topics: dict
