# The rules of the trusted medical source library. The controller calls these; the SQL is in repository.py.
#
# - Only an Admin calls them (controller.py checks the token).
# - A new source starts as "draft": the AI cannot cite it.
# - Only "approve" makes a source "active", and it needs the client's written approval (who, the message, the date).
# - Changing an active source needs the client's approval of that change too; without it the change is refused.
# - Sources are never deleted: "deactivate" sets them to "inactive", so past results keep their source.
# - Every change writes one ai.audit_log row (who, what, why, before and after) in the same transaction.

from datetime import date, datetime, time, timezone
from typing import List, Optional

from app.medical_sources import library
from app.medical_sources.constants import ACTIVE, INACTIVE, NEW_SOURCE_STATUS, TOPICS
from app.medical_sources.repository import open_repository
from app.medical_sources.schemas import (
    ApproveRequest, ClientApproval, DeactivateRequest, SourceCreate, SourceUpdate,
)
from app.shared.jobs import AuditLogRow
from python_auth.auth import AuthUser

RESOURCE_TYPE = "medical_source"

# Fields that change what the AI or the patient sees. Changing one on an active source needs the client's approval.
CONTENT_FIELDS = {
    "organisation", "authors", "title", "year", "publication", "doi", "link", "topics", "motors",
    "patient_label", "evidence_level", "language",
}


class SourceError(Exception):
    """A refused request: the controller answers `status_code` with {"detail": message}."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def not_found(source_id: str) -> SourceError:
    return SourceError(404, f"Medical source '{source_id}' not found")


def approval_time(approved_at: Optional[date]) -> datetime:
    """The approval date as a timestamp (midnight UTC), or now when no date was given."""
    if approved_at is None:
        return datetime.now(timezone.utc)
    return datetime.combine(approved_at, time.min, tzinfo=timezone.utc)


def approval_values(approval: ClientApproval) -> dict:
    return {
        "approved_by": approval.approved_by,
        "approval_note": approval.approval_note,
        "approved_at": approval_time(approval.approved_at),
    }


def audit_values(source: Optional[dict]) -> Optional[dict]:
    """A source as JSON for the audit log (dates become text)."""
    if source is None:
        return None
    return {key: value.isoformat() if isinstance(value, (date, datetime)) else value for key, value in source.items()}


def audit_row(user: AuthUser, action: str, source_id: str, reason: str, before=None, after=None) -> AuditLogRow:
    return AuditLogRow(
        actor_id=user.user_id,
        actor_role="admin",  # only Admin tokens reach the service (controller.py)
        action=action,
        resource_type=RESOURCE_TYPE,
        resource_id=source_id,
        reason=reason,
        before=audit_values(before),
        after=audit_values(after),
    )


def changed_fields(before: dict, after: dict) -> List[str]:
    """The fields whose value changed (updated_at always changes, so it is left out)."""
    return sorted(key for key in after if key in before and key != "updated_at" and before[key] != after[key])


def only_changes(before: dict, after: dict) -> dict:
    """The audit before/after values of the fields that changed, so the log stays short."""
    fields = changed_fields(before, after)
    return {key: before[key] for key in fields}, {key: after[key] for key in fields}


# ---------- reads ----------

SEED_ACTOR = "seed"
SEED_NAME = "System (seed)"


def actor_label(actor_id: Optional[str], name: Optional[str]) -> Optional[str]:
    """What a person should read for created_by / updated_by: the admin's name, never a bare MongoDB id
    when the name is known."""
    if name:
        return name
    if actor_id == SEED_ACTOR:
        return SEED_NAME
    return actor_id


def present(source: dict) -> dict:
    """A source as the API returns it: created_by / updated_by are names, the ids move to *_by_id."""
    out = dict(source)
    for prefix in ("created", "updated"):
        actor_id = out.get(f"{prefix}_by")
        name = out.pop(f"{prefix}_by_name", None)
        out[f"{prefix}_by_id"] = actor_id
        out[f"{prefix}_by"] = actor_label(actor_id, name)
    return out


def actor_values(user: AuthUser, *prefixes: str) -> dict:
    """created_by/updated_by (the id, for the audit trail) and their *_name (for people)."""
    values = {}
    for prefix in prefixes:
        values[f"{prefix}_by"] = user.user_id
        values[f"{prefix}_by_name"] = user.name
    return values


async def list_sources(
    country: Optional[str] = None,
    status: Optional[str] = None,
    motor: Optional[str] = None,
    topic: Optional[str] = None,
    search: Optional[str] = None,
    sort: str = "created_at",
    order: str = "desc",
    page: Optional[int] = None,
    limit: Optional[int] = None,
) -> dict:
    """Newest first by default. page/limit are optional: without them every matching source is returned."""
    search = (search or "").strip() or None
    offset = (page - 1) * limit if page and limit else 0
    async with open_repository() as repository:
        rows, total = await repository.list(
            country=country, status=status, motor=motor, topic=topic, search=search,
            sort=sort, order=order, limit=limit, offset=offset,
        )
    result = {"count": total, "sources": [present(row) for row in rows]}
    if limit:
        result.update(page=page or 1, limit=limit, total_pages=max(1, -(-total // limit)))
    return result


async def get_source(source_id: str) -> dict:
    async with open_repository() as repository:
        source = await repository.get(source_id)
    if source is None:
        raise not_found(source_id)
    return present(source)


def list_topics() -> dict:
    return dict(TOPICS)


# ---------- changes ----------

async def create_source(request: SourceCreate, user: AuthUser) -> dict:
    values = request.model_dump(exclude={"reason"}, exclude_none=True)
    values.update(status=NEW_SOURCE_STATUS, **actor_values(user, "created", "updated"))
    async with open_repository() as repository:
        if await repository.get(request.id) is not None:
            raise SourceError(409, f"Medical source '{request.id}' already exists; change it with PATCH")
        source = await repository.insert(values)
        await repository.add_audit(audit_row(
            user, "create", source["id"], request.reason or "Medical source added (draft)", after=source,
        ))
    library.clear_cache()
    return present(source)


async def update_source(source_id: str, request: SourceUpdate, user: AuthUser) -> dict:
    changes = request.model_dump(exclude={"approval", "reason"}, exclude_unset=True)
    if not changes:
        raise SourceError(400, "Send at least one field to change")
    for field in ("organisation", "title", "link", "topics", "motors", "patient_label", "language", "evidence_level"):
        if field in changes and changes[field] is None:
            raise SourceError(422, f"{field} cannot be empty")

    async with open_repository() as repository:
        before = await repository.get(source_id, for_update=True)
        if before is None:
            raise not_found(source_id)
        content_changed = [field for field in changes if field in CONTENT_FIELDS and changes[field] != before[field]]
        if before["status"] == ACTIVE and content_changed:
            if request.approval is None:
                raise SourceError(409, (
                    f"'{source_id}' is active, so changing {', '.join(sorted(content_changed))} needs the client's "
                    "written approval of the change: send it in \"approval\", or deactivate the source first"
                ))
            changes.update(approval_values(request.approval))
        changes.update(actor_values(user, "updated"))
        after = await repository.update(source_id, changes)
        old, new = only_changes(before, after)
        await repository.add_audit(audit_row(
            user, "update", source_id, request.reason or "Medical source changed", before=old, after=new,
        ))
    library.clear_cache()
    return present(after)


async def approve_source(source_id: str, request: ApproveRequest, user: AuthUser) -> dict:
    async with open_repository() as repository:
        before = await repository.get(source_id, for_update=True)
        if before is None:
            raise not_found(source_id)
        if before["status"] == ACTIVE:
            raise SourceError(409, f"'{source_id}' is already active")
        changes = {
            **approval_values(request),
            "status": ACTIVE,
            "client_decision": "approve",
            "needs_client_confirmation": False,  # the written approval answers the open question
            **actor_values(user, "updated"),
        }
        after = await repository.update(source_id, changes)
        old, new = only_changes(before, after)
        await repository.add_audit(audit_row(
            user, "update", source_id, f"Approved by {request.approved_by}: {request.approval_note}"[:1000],
            before=old, after=new,
        ))
    library.clear_cache()
    return present(after)


async def deactivate_source(source_id: str, request: DeactivateRequest, user: AuthUser) -> dict:
    async with open_repository() as repository:
        before = await repository.get(source_id, for_update=True)
        if before is None:
            raise not_found(source_id)
        if before["status"] == INACTIVE:
            raise SourceError(409, f"'{source_id}' is already inactive")
        after = await repository.update(source_id, {"status": INACTIVE, **actor_values(user, "updated")})
        old, new = only_changes(before, after)
        await repository.add_audit(audit_row(user, "update", source_id, request.reason, before=old, after=new))
    library.clear_cache()
    return present(after)
