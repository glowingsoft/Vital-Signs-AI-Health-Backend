# Checks that the caller has given the consents a request needs, before any motor is called.
# Consents live in ai.consents (written by Node, append-only): the newest row per user and type is
# the current state, so a withdrawal is a newer row with granted = false.

import logging
from typing import List

from fastapi import HTTPException
from sqlalchemy import text

from app import config
from app.configurations.consent import CONSENT_MESSAGES, REQUIRED_CONSENTS
from app.shared.contracts import AssessRequest
from app.shared.db import get_session_factory

logger = logging.getLogger(__name__)

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
        raise HTTPException(status_code=503, detail={"error": "consent_check_unavailable"})
    if missing:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "consent_required",
                "missing": missing,
                # A type added to REQUIRED_CONSENTS without a message still gets a readable one.
                "messages": [
                    CONSENT_MESSAGES.get(consent, f"Please give the '{consent}' consent in the app.")
                    for consent in missing
                ],
            },
        )
