# Daily check of the links of the active medical sources (ai.medical_sources).
#
#   python -m app.cron_jobs.main --job source_check
#
# 1. Read every source with status 'active' (the ones the AI may cite).
# 2. Open each link (GET, redirects followed, 15 s, browser User-Agent), at most 3 at a time.
#      200-399                                       -> ok
#      404, 410, 5xx, timeout, DNS, connection, SSL  -> broken
#      401, 403, 429 and any other code              -> blocked: the publisher refuses automated checks,
#                                                       so we know nothing; the source is left unchanged
#    Only the HTTP result is used: the page itself is never read or stored.
# 3. Write link_status ('ok' or 'broken'), link_checked_at = today and updated_by = 'system:source_check'.
#    Broken twice in a row (link_status was already 'broken') -> status = 'broken': library.py gives the AI
#    only 'active' sources, so it stops citing it and results fall back to "pending medical review".
#    Nothing here sets a source back to 'active': only an admin does that (POST /approve, client's approval).
# 4. One ai.audit_log row per source whose link_status or status changed, in the same transaction.
# 5. A WARNING for every source set to broken, and one email per run if LINK_ALERT_EMAILS is set.
#    Admins can list them with GET /ai/admin/medical-sources?status=broken.

import asyncio
import logging
import socket
import ssl
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Dict, List, Optional

import httpx

from app import config
from app.medical_sources import library
from app.medical_sources.constants import ACTIVE
from app.medical_sources.repository import MedicalSourceRepository, open_system_session
from app.shared.jobs import AuditLogRow

logger = logging.getLogger(__name__)

ACTOR = "system:source_check"
RESOURCE_TYPE = "medical_source"
OK, BROKEN, BLOCKED = "ok", "broken", "blocked"
TIMEOUT_SECONDS = 15
MAX_PARALLEL = 3
# Some publishers refuse requests from unknown clients, so the check looks like a normal browser.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
BROKEN_CODES = {404, 410}  # plus every 5xx


class LinkCheckFailed(Exception):
    """The check ran but could not record every result or send the alert: the cron job exits with 1."""


@dataclass
class LinkResult:
    source_id: str
    link: str
    outcome: str  # OK, BROKEN or BLOCKED
    detail: str  # e.g. "200 OK", "404 Not Found", "timeout after 15 s"


# ---------- 1. the HTTP check ----------

def describe_status(code: int) -> str:
    return f"{code} {httpx.codes.get_reason_phrase(code)}".strip()


def outcome_for_status(code: int) -> str:
    if 200 <= code <= 399:
        return OK
    if code in BROKEN_CODES or code >= 500:
        return BROKEN
    return BLOCKED  # 401, 403, 429 and other codes: not proof the link is gone


def describe_error(error: Exception) -> str:
    """A short reason for a failed request: timeout, SSL, DNS or connection error."""
    if isinstance(error, httpx.TimeoutException):
        return f"timeout after {TIMEOUT_SECONDS} s"
    if isinstance(error, httpx.TooManyRedirects):
        return "too many redirects"
    cause = error
    while cause is not None:  # httpx wraps the low-level error: look at its causes
        if isinstance(cause, ssl.SSLError):
            return f"SSL error: {cause}"
        if isinstance(cause, socket.gaierror):
            return "DNS error: host not found"
        cause = cause.__cause__ or cause.__context__
    if "CERTIFICATE" in str(error).upper() or "SSL" in str(error).upper():
        return f"SSL error: {error}"
    return f"connection error: {type(error).__name__}: {error}"[:300]


def make_http_client() -> httpx.AsyncClient:
    # Kept in its own function so tests can swap in a client with a fake transport.
    return httpx.AsyncClient(
        follow_redirects=True, timeout=TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT},
    )


async def check_link(client: httpx.AsyncClient, source_id: str, link: str) -> LinkResult:
    try:
        # stream = get the status line and headers only; the body (the page) is never downloaded or read.
        async with client.stream("GET", link) as response:
            code = response.status_code
    except httpx.HTTPError as error:
        return LinkResult(source_id, link, BROKEN, describe_error(error))
    return LinkResult(source_id, link, outcome_for_status(code), describe_status(code))


async def check_links(client: httpx.AsyncClient, sources: List[dict]) -> List[LinkResult]:
    """Check every link, at most MAX_PARALLEL at a time. Results come back in the order of sources."""
    limit = asyncio.Semaphore(MAX_PARALLEL)

    async def one(source: dict) -> LinkResult:
        async with limit:
            result = await check_link(client, source["id"], source["link"])
        log = logger.warning if result.outcome == BROKEN else logger.info
        log("Link %s: %s (%s) %s", result.outcome, result.source_id, result.detail, result.link)
        return result

    return list(await asyncio.gather(*(one(source) for source in sources)))


# ---------- 2. what to write ----------

def decide(state: dict, result: LinkResult) -> dict:
    """The new link_status, whether the source is set to broken, and the audit reason. Pure, no database.
    state = the source's current status and link_status; result = an OK or BROKEN check."""
    was_broken = state.get("link_status") == BROKEN
    if result.outcome == OK:
        reason = f"Link check: link works again ({result.detail})" if was_broken else f"Link check: {result.detail}"
        return {"link_status": OK, "set_broken": False, "reason": reason}
    if was_broken:
        reason = f"Link check failed twice: source set to broken ({result.detail})"
        return {"link_status": BROKEN, "set_broken": True, "reason": reason}
    return {"link_status": BROKEN, "set_broken": False, "reason": f"Link check: {result.detail} (1st failure)"}


def json_value(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def changes(before: dict, after: dict) -> Dict[str, dict]:
    """{"before": ..., "after": ...} with only the fields whose value changed."""
    fields = [field for field in ("status", "link_status", "link_checked_at", "updated_by") if before.get(field) != after.get(field)]
    return {
        "before": {field: json_value(before.get(field)) for field in fields},
        "after": {field: json_value(after.get(field)) for field in fields},
    }


async def record(session_factory, result: LinkResult, today: date) -> str:
    """Write one result in its own transaction (update + audit row together).
    Returns "ok", "failed_once", "set_broken" or "skipped" (no longer active: an admin changed it meanwhile)."""
    async with session_factory() as session, session.begin():
        repository = MedicalSourceRepository(session)
        before = await repository.get_link_state(result.source_id)
        if before is None or before["status"] != ACTIVE:
            logger.info("Link check: %s is no longer active, left unchanged", result.source_id)
            return "skipped"
        decision = decide(before, result)
        after = await repository.record_link_check(
            result.source_id, decision["link_status"], today, decision["set_broken"], ACTOR,
        )
        if after is None:
            return "skipped"
        # An audit row only when something that matters changed (an ok link that is still ok writes none).
        if before["link_status"] != after["link_status"] or before["status"] != after["status"]:
            await repository.add_audit(AuditLogRow(
                actor_id=ACTOR,
                actor_role="system",
                action="update",
                resource_type=RESOURCE_TYPE,
                resource_id=result.source_id,
                reason=decision["reason"],
                **changes(before, after),
            ))
    if decision["set_broken"]:
        return "set_broken"
    return "failed_once" if decision["link_status"] == BROKEN else "ok"


# ---------- 3. the alert ----------

def alert_text(broken: List[LinkResult]) -> str:
    lines = [
        "The daily link check set these medical sources to 'broken' (their link failed twice in a row).",
        "The AI no longer cites them; results fall back to \"pending medical review\".",
        "",
    ]
    lines += [f"- {result.source_id}: {result.link} ({result.detail})" for result in broken]
    lines += [
        "",
        "List them in the admin panel: GET /ai/admin/medical-sources?status=broken",
        "Fix the link and approve the source again (POST /approve, with the client's approval) to make it active.",
    ]
    return "\n".join(lines)


def send_alert_email(broken: List[LinkResult]) -> None:
    """One email through AWS SES listing the sources set to broken. Credentials: the standard AWS chain."""
    import boto3

    boto3.client("ses", region_name=config.AWS_REGION).send_email(
        Source=config.LINK_ALERT_FROM,
        Destination={"ToAddresses": list(config.LINK_ALERT_EMAILS)},
        Message={
            "Subject": {"Data": f"Medical sources set to broken: {len(broken)}"},
            "Body": {"Text": {"Data": alert_text(broken)}},
        },
    )


async def alert(broken: List[LinkResult]) -> Optional[str]:
    """Log the WARNING and send the email if configured. Returns an error message when the email failed."""
    if not broken:
        return None
    logger.warning(
        "Medical sources set to broken (%d): %s", len(broken),
        "; ".join(f"{result.source_id} {result.link} ({result.detail})" for result in broken),
    )
    if not config.LINK_ALERT_EMAILS:
        return None
    if not config.LINK_ALERT_FROM:
        return "LINK_ALERT_EMAILS is set but LINK_ALERT_FROM is empty: alert email not sent"
    try:
        await asyncio.to_thread(send_alert_email, broken)  # boto3 blocks: run it in a thread
    except Exception as error:
        return f"alert email not sent: {error!r}"
    logger.info("Alert email sent to %s", ", ".join(config.LINK_ALERT_EMAILS))
    return None


# ---------- the job ----------

async def run(today: Optional[date] = None) -> dict:
    """The cron job. Returns the summary; raises LinkCheckFailed (after finishing everything it can)
    when a result could not be saved or the alert email failed."""
    today = today or datetime.now(timezone.utc).date()
    summary = {"checked": 0, "ok": 0, "failed_once": 0, "set_broken": 0, "blocked": 0}
    problems = []
    broken = []

    async with open_system_session() as session_factory:
        async with session_factory() as session:
            sources = await MedicalSourceRepository(session).list_active_links()
        async with make_http_client() as client:
            results = await check_links(client, sources)

        for result in results:
            summary["checked"] += 1
            if result.outcome == BLOCKED:
                summary["blocked"] += 1  # the publisher refused the check: the source is left unchanged
                continue
            try:
                written = await record(session_factory, result, today)
            except Exception as error:
                logger.exception("Link check: could not save the result of %s", result.source_id)
                problems.append(f"{result.source_id}: {error!r}")
                continue
            if written in summary:
                summary[written] += 1
            if written == "set_broken":
                broken.append(result)

    library.clear_cache()  # this process; the motors read the table again within MEDICAL_SOURCES_CACHE_SECONDS
    problem = await alert(broken)
    if problem:
        problems.append(problem)

    print(
        f"Medical source link check: {summary['checked']} checked, {summary['ok']} ok, "
        f"{summary['failed_once']} failed once, {summary['set_broken']} set to broken, {summary['blocked']} blocked"
    )
    if problems:
        raise LinkCheckFailed(f"{summary}; problems: {'; '.join(problems)}")
    return summary
