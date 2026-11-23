# Checks that the caller has given the consents a request needs, before any motor is called.
# Consents live in ai.consents (written by Node, append-only): the newest row per user and type is
# the current state, so a withdrawal is a newer row with granted = false.

import logging
from typing import List

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import config
from app.configurations.consent import CONSENT_MESSAGES, CONSENT_REQUIRED_MESSAGE, REQUIRED_CONSENTS
from app.shared.contracts import AssessRequest
from app.shared.db import get_session_factory

logger = logging.getLogger(__name__)


class ConsentError(Exception):
    """Refused because of consent. Answered by consent_error_response() as
    {"code": ..., "message": ..., "missing": [{"type": ..., "message": ...}]}, without FastAPI's "detail" wrapper."""

    def __init__(self, status_code: int, code: str, message: str, missing: List[str] = ()):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.missing = list(missing)


async def consent_error_response(request: Request, error: ConsentError) -> JSONResponse:
    """Registered in main.py with app.add_exception_handler(ConsentError, ...)."""
    return JSONResponse(
        status_code=error.status_code,
        content={
            "code": error.code,
            "message": error.message,
            "missing": [
                # A type added to REQUIRED_CONSENTS without a message still gets a readable one.
                {"type": consent, "message": CONSENT_MESSAGES.get(consent, f"Please give the '{consent}' consent in the app.")}
                for consent in error.missing
            ],
        },
    )

# DISTINCT ON keeps the first row per consent_type, and ORDER BY makes that the newest one.
# Uses the consents_user_type_time_idx index (user_id, consent_type, created_at DESC).
NEWEST_CONSENTS_SQL = text(
    """
    SELECT DISTINCT ON (consent_type) consent_type, granted
    FROM ai.consents
    WHERE user_id = :user_id AND consent_type = ANY(:types)
    ORDER BY consent_type, created_at DESC
    """
)


async def missing_consents(user_id: str, needed: List[str]) -> List[str]:
    """The needed consent types the user has not given: no row at all, or the newest row has granted = false."""
    if not needed:
        return []
    async with get_session_factory()() as session:
        rows = (await session.execute(NEWEST_CONSENTS_SQL, {"user_id": user_id, "types": needed})).all()
    granted = {row.consent_type for row in rows if row.granted}
    return [consent for consent in needed if consent not in granted]


def needed_consents(request: AssessRequest) -> List[str]:
    """Consents needed for the parts present in the request, without duplicates, in REQUIRED_CONSENTS order."""
    parts = [part for part in REQUIRED_CONSENTS if getattr(request, part)]
    needed = []
    for part in parts:
        for consent in REQUIRED_CONSENTS[part]:
            if consent not in needed:
                needed.append(consent)
    return needed


async def check_consent(user_id: str, request: AssessRequest) -> None:
    """403 if a needed consent is missing, 503 if the database cannot be asked.
    user_id = the caller's id from the verified token, never from the request body."""
    if not config.CONSENT_CHECK_ENABLED:
        return
    try:
        missing = await missing_consents(user_id, needed_consents(request))
    except Exception as error:
        # Never skip the check when the database is down: refusing is the safe answer.
        logger.error("Consent check failed: %r", error)
        raise ConsentError(503, "consent_check_unavailable", "The consent check is not available right now. Please try again.")
    if missing:
        raise ConsentError(403, "consent_required", CONSENT_REQUIRED_MESSAGE, missing)
