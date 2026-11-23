# What the motors use at run time (Addendum A, medical-source field):
#
# 1. The AI is given only the sources for the patient's country (US or MX) and its own motor.
# 2. The AI picks a source id from that list, or "none".
# 3. The backend checks the id is in the list and attaches the exact title and link itself. Anything else is ignored.
# 4. No match: no reference, and the result is marked "pending_medical_review". A link is never invented.
#
# The list is read from ai.medical_sources (managed through the admin API, controller.py) and kept in memory for
# config.MEDICAL_SOURCES_CACHE_SECONDS. Only sources whose status is in config.MEDICAL_SOURCE_STATUSES are used.
# Without a database configured (DB_NAME empty: tests, a quick local run) the seed file
# db/medical_sources/medical_sources.json is read instead.

import json
import logging
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from app import config
from app.medical_sources.repository import read_all_sources_sync

logger = logging.getLogger(__name__)

LIBRARY_FILE = Path(__file__).resolve().parent.parent.parent / "db" / "medical_sources" / "medical_sources.json"

NO_SOURCE = "none"  # what the AI answers when no source in its list fits
MATCHED = "matched"
PENDING_MEDICAL_REVIEW = "pending_medical_review"

# Countries the library covers, and the spellings the app may send in context.country.
COUNTRY_ALIASES = {
    "US": "US", "USA": "US", "UNITED STATES": "US", "UNITED STATES OF AMERICA": "US", "ESTADOS UNIDOS": "US",
    "MX": "MX", "MEX": "MX", "MEXICO": "MX", "MÉXICO": "MX",
}

RETRY_SECONDS = 30  # database down before the first read: how long until it is tried again

_lock = threading.Lock()
_cache: Dict[str, object] = {"sources": None, "loaded_at": 0.0}


def normalize_country(country: Optional[str]) -> Optional[str]:
    """"Mexico", "mx", "MÉXICO" -> "MX"; "United States", "us" -> "US"; anything else -> None (no sources)."""
    if not country:
        return None
    return COUNTRY_ALIASES.get(country.strip().upper())


def read_from_database() -> List[dict]:
    """Every row of ai.medical_sources. Raises when the database cannot be reached."""
    return read_all_sources_sync()


def read_from_file(path: Path = LIBRARY_FILE) -> List[dict]:
    """The sources in the seed file, or [] when the file is not there (it is not in the Docker image)."""
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["sources"]


def load_sources() -> List[dict]:
    """Every source in the library, any status. The database when DB_NAME is set, otherwise the seed file.
    When the database is down, the last list read is kept; with none, there are no sources (every result is then
    "pending_medical_review") until the database answers again."""
    with _lock:
        cached = _cache["sources"]
        if cached is not None and time.monotonic() - _cache["loaded_at"] < config.MEDICAL_SOURCES_CACHE_SECONDS:
            return cached
        loaded_at = time.monotonic()
        if config.DB_NAME:
            try:
                sources = read_from_database()
            except Exception as error:
                logger.warning("Medical sources: database not reachable (%s)", error)
                if cached is None:
                    # Nothing read yet: no sources for now, and try again after RETRY_SECONDS, not a full cache time.
                    sources = []
                    loaded_at -= max(config.MEDICAL_SOURCES_CACHE_SECONDS - RETRY_SECONDS, 0)
                else:
                    sources = cached
        else:
            sources = read_from_file()
        _cache["sources"], _cache["loaded_at"] = sources, loaded_at
        return sources


def clear_cache() -> None:
    """Read the library again on the next call (after an admin change, and in tests)."""
    with _lock:
        _cache["sources"], _cache["loaded_at"] = None, 0.0


def sources_for(motor: str, country: Optional[str]) -> List[dict]:
    """The sources the AI may pick from: patient's country, this motor, allowed status."""
    code = normalize_country(country)
    if code is None:
        return []
    return [
        source for source in load_sources()
        if source["country"] == code
        and motor in source["motors"]
        and source["status"] in config.MEDICAL_SOURCE_STATUSES
    ]


def prompt_block(motor: str, country: Optional[str]) -> str:
    """The source list added to the system prompt: one line per source, id | organisation | title | topics.
    Titles and links are not needed by the AI: the backend attaches them."""
    sources = sources_for(motor, country)
    if not sources:
        return f'\nTrusted source list: empty. Set "source_id" to "{NO_SOURCE}".'
    lines = [
        f'- {source["id"]} | {source["organisation"]} | {source["title"]} ({source["year"]}) | '
        f'topics: {", ".join(source["topics"])}'
        for source in sources
    ]
    return "\nTrusted source list (pick only from these ids):\n" + "\n".join(lines)


def full_citation(source: dict) -> str:
    """The complete citation the doctor sees: organisation or authors, title, publication, year, DOI and link."""
    parts = [source.get("authors") or source["organisation"], source["title"]]
    if source.get("publication"):
        parts.append(source["publication"])
    elif source.get("year"):
        parts.append(str(source["year"]))
    if source.get("doi"):
        parts.append(f'doi:{source["doi"]}')
    return ". ".join(part.strip().rstrip(".") for part in parts) + ". " + source["link"]  # no "." after the link


def check_source(source_id: Optional[str], motor: str, country: Optional[str]) -> dict:
    """The check after the AI answer. Returns the MotorOutput source fields:
    matched  -> source_id, the library's evidence_level, the full citation as reference, the patient label;
    no match -> no source, no reference, evidence_level and source_status "pending_medical_review"."""
    allowed = {source["id"]: source for source in sources_for(motor, country)}
    picked = source_id.strip() if isinstance(source_id, str) else ""
    source = allowed.get(picked)
    if source is None:
        if picked and picked.lower() != NO_SOURCE:
            logger.warning("Motor %s: AI gave source %r, not in the %s list: ignored", motor, picked[:80], country)
        return {
            "source_id": None,
            "source_status": PENDING_MEDICAL_REVIEW,
            "evidence_level": PENDING_MEDICAL_REVIEW,
            "reference": None,
            "source_label": None,
            "source_link": None,
        }
    return {
        "source_id": source["id"],
        "source_status": MATCHED,
        "evidence_level": source["evidence_level"],
        "reference": full_citation(source),
        "source_label": source["patient_label"],
        "source_link": source["link"],
    }
