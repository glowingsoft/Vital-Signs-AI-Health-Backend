# First load of the trusted medical source library from the seed file db/medical_sources/medical_sources.json.
# Run it once on a new database: python -m scripts.load_medical_sources
#
# It only INSERTS sources whose id is not in ai.medical_sources yet. It never changes or deletes a row, so it
# never overwrites what an admin did through the API (controller.py). After the first load, sources are added
# and changed only through the API.

import json
import os
from pathlib import Path
from typing import List

from app import config
from app.medical_sources.constants import COUNTRIES, EVIDENCE_LEVELS, ID_PATTERN, SOURCE_MOTORS, STATUSES, TOPICS
from app.medical_sources.library import LIBRARY_FILE

REQUIRED = ["id", "country", "language", "organisation", "title", "link", "topics", "motors", "patient_label", "status"]

INSERT_IF_NEW = """
    INSERT INTO ai.medical_sources (
        id, country, language, organisation, authors, title, year, publication, doi, link,
        topics, motors, patient_label, evidence_level, status, client_decision, needs_client_confirmation,
        current_note, notes, link_status, link_checked_at, created_by, updated_by,
        created_by_name, updated_by_name
    ) VALUES (
        %(id)s, %(country)s, %(language)s, %(organisation)s, %(authors)s, %(title)s, %(year)s, %(publication)s,
        %(doi)s, %(link)s, %(topics)s, %(motors)s, %(patient_label)s, %(evidence_level)s, %(status)s,
        %(client_decision)s, %(needs_client_confirmation)s, %(current_note)s, %(notes)s, %(link_status)s,
        %(link_checked_at)s, 'seed', 'seed', 'System (seed)', 'System (seed)'
    )
    ON CONFLICT (id) DO NOTHING
    RETURNING id
"""


def check_library(library: dict) -> List[str]:
    """Every problem in the seed file, or [] when it can be loaded."""
    problems = []
    seen = set()
    for number, source in enumerate(library.get("sources", []), start=1):
        name = source.get("id") or f"source #{number}"
        missing = [field for field in REQUIRED if not source.get(field)]
        if missing:
            problems.append(f"{name}: missing {', '.join(missing)}")
            continue
        if name in seen:
            problems.append(f"{name}: id used twice")
        seen.add(name)
        if not ID_PATTERN.match(name) or not name.startswith(f"{source['country']}-"):
            problems.append(f"{name}: id must look like US-AHA-HBP-2025 and start with its country")
        if source["country"] not in COUNTRIES:
            problems.append(f"{name}: country must be one of {COUNTRIES}")
        if source["status"] not in STATUSES:
            problems.append(f"{name}: status must be one of {STATUSES}")
        if source["status"] == "active":
            problems.append(f"{name}: the seed cannot make a source active; approve it through the API")
        if source.get("evidence_level", "clinical_guideline") not in EVIDENCE_LEVELS:
            problems.append(f"{name}: evidence_level must be one of {EVIDENCE_LEVELS}")
        if not source["link"].startswith("https://"):
            problems.append(f"{name}: link must start with https://")
        if set(source["motors"]) - set(SOURCE_MOTORS):
            problems.append(f"{name}: motors must be among {SOURCE_MOTORS}")
        unknown = set(source["topics"]) - set(TOPICS)
        if unknown:
            problems.append(f"{name}: unknown topics {sorted(unknown)} (allowed: app/medical_sources/constants.py)")
    if not seen and not problems:
        problems.append("the file has no sources")
    return problems


def row(source: dict) -> dict:
    """One source as the parameters of INSERT_IF_NEW."""
    link_check = source.get("link_check") or {}
    return {
        "id": source["id"],
        "country": source["country"],
        "language": source["language"],
        "organisation": source["organisation"],
        "authors": source.get("authors"),
        "title": source["title"],
        "year": source.get("year"),
        "publication": source.get("publication"),
        "doi": source.get("doi"),
        "link": source["link"],
        "topics": list(source["topics"]),
        "motors": list(source["motors"]),
        "patient_label": source["patient_label"],
        "evidence_level": source.get("evidence_level") or "clinical_guideline",
        "status": source["status"],
        "client_decision": source.get("client_decision"),
        "needs_client_confirmation": bool(source.get("needs_client_confirmation")),
        "current_note": source.get("current"),
        "notes": source.get("notes"),
        "link_status": link_check.get("status"),
        "link_checked_at": link_check.get("checked_at"),
    }


def connect():
    import psycopg

    url = os.environ.get("DATABASE_URL")
    if url:
        # Same URL Alembic reads; psycopg wants plain postgresql://, without a SQLAlchemy driver name.
        return psycopg.connect(url.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg://", "postgresql://"))
    return psycopg.connect(
        host=config.DB_HOST, port=config.DB_PORT, dbname=config.DB_NAME,
        user=config.DB_APP_USER, password=config.DB_APP_PASSWORD, connect_timeout=5,
    )


def seed(library: dict) -> dict:
    """Insert the sources that are not in the table yet, in one transaction. Returns how many were added and skipped."""
    counts = {"added": 0, "skipped": 0}
    with connect() as connection:  # commits at the end, rolls back on any error
        with connection.cursor() as cursor:
            for source in library["sources"]:
                cursor.execute(INSERT_IF_NEW, row(source))
                counts["added" if cursor.fetchone() else "skipped"] += 1
    return counts


def read_seed_file(path: Path = LIBRARY_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
