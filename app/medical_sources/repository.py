# The SQL on ai.medical_sources (and the audit row for each change). Plain SQL with named parameters (:name);
# values are sent separately, never pasted into the SQL. No rules here: those are in service.py.
#
# MedicalSourceRepository is used by the admin API (async, one transaction per request).
# read_all_sources_sync() is used by the motors (sync routes), through library.py.

import os
from contextlib import asynccontextmanager
from datetime import date
from typing import AsyncIterator, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import config
from app.shared.db import build_url, get_session_factory
from app.shared.jobs import AuditLogRow
from app.worker.repository import insert_audit_log

COLUMNS = [
    "id", "country", "language", "organisation", "authors", "title", "year", "publication", "doi", "link",
    "topics", "motors", "patient_label", "evidence_level", "status", "client_decision", "needs_client_confirmation",
    "approved_by", "approved_at", "approval_note", "current_note", "notes", "link_status", "link_checked_at",
    "created_by", "updated_by", "created_at", "updated_at",
]
SELECT_COLUMNS = ", ".join(COLUMNS)

# Columns the API may write. id, created_at and updated_at are set here, never from a request.
WRITABLE = [column for column in COLUMNS if column not in ("id", "created_at", "updated_at")]


class MedicalSourceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list(
        self,
        country: Optional[str] = None,
        status: Optional[str] = None,
        motor: Optional[str] = None,
        topic: Optional[str] = None,
    ) -> List[dict]:
        # Each filter is skipped when it is NULL, so one statement covers every combination.
        result = await self.session.execute(text(f"""
            SELECT {SELECT_COLUMNS} FROM ai.medical_sources
            WHERE (CAST(:country AS text) IS NULL OR country = :country)
              AND (CAST(:status AS text) IS NULL OR status = :status)
              AND (CAST(:motor AS text) IS NULL OR :motor = ANY(motors))
              AND (CAST(:topic AS text) IS NULL OR :topic = ANY(topics))
            ORDER BY country, id
        """), {"country": country, "status": status, "motor": motor, "topic": topic})
        return [dict(row._mapping) for row in result]

    async def get(self, source_id: str, for_update: bool = False) -> Optional[dict]:
        """One source, or None. for_update=True locks the row until the transaction ends (no lost updates)."""
        lock = " FOR UPDATE" if for_update else ""
        result = await self.session.execute(
            text(f"SELECT {SELECT_COLUMNS} FROM ai.medical_sources WHERE id = :id{lock}"), {"id": source_id}
        )
        row = result.first()
        return dict(row._mapping) if row else None

    async def insert(self, values: dict) -> dict:
        """Insert a new source. values = id plus WRITABLE columns (missing ones use the table default or NULL)."""
        columns = ["id"] + [column for column in WRITABLE if column in values]
        result = await self.session.execute(text(f"""
            INSERT INTO ai.medical_sources ({", ".join(columns)})
            VALUES ({", ".join(":" + column for column in columns)})
            RETURNING {SELECT_COLUMNS}
        """), {column: values[column] for column in columns})
        return dict(result.first()._mapping)

    async def update(self, source_id: str, changes: dict) -> dict:
        """Change the given WRITABLE columns; updated_at becomes now."""
        unknown = set(changes) - set(WRITABLE)
        if unknown:
            raise ValueError(f"not writable: {sorted(unknown)}")
        assignments = ", ".join(f"{column} = :{column}" for column in changes)
        result = await self.session.execute(text(f"""
            UPDATE ai.medical_sources SET {assignments}, updated_at = now()
            WHERE id = :id
            RETURNING {SELECT_COLUMNS}
        """), {**changes, "id": source_id})
        return dict(result.first()._mapping)

    async def add_audit(self, row: AuditLogRow) -> None:
        await insert_audit_log(self.session, row)

    # ---------- daily link check (app/cron_jobs/source_check/check_links.py) ----------

    async def list_active_links(self) -> List[dict]:
        """id and link of every active source: the ones the AI may cite, so the ones whose link must work."""
        result = await self.session.execute(text(
            "SELECT id, link FROM ai.medical_sources WHERE status = 'active' ORDER BY id"
        ))
        return [dict(row._mapping) for row in result]

    async def get_link_state(self, source_id: str) -> Optional[dict]:
        """status and the last link check of one source, locked until the transaction ends, so an admin change
        made while the links were being checked is seen before the result is written."""
        result = await self.session.execute(text("""
            SELECT id, status, link_status, link_checked_at, updated_by FROM ai.medical_sources WHERE id = :id FOR UPDATE
        """), {"id": source_id})
        row = result.first()
        return dict(row._mapping) if row else None

    async def record_link_check(
        self, source_id: str, link_status: str, checked_on: date, set_broken: bool, updated_by: str,
    ) -> Optional[dict]:
        """Write one link check result. Only link_status, link_checked_at, updated_by and (set_broken) status change,
        and only on a source that is still active: status can go from active to broken here, never back.
        Returns the new values, or None when the source is no longer active (nothing written)."""
        result = await self.session.execute(text("""
            UPDATE ai.medical_sources
            SET link_status = :link_status,
                link_checked_at = :checked_on,
                status = CASE WHEN :set_broken THEN 'broken' ELSE status END,
                updated_by = :updated_by,
                updated_at = now()
            WHERE id = :id AND status = 'active'
            RETURNING id, status, link_status, link_checked_at, updated_by
        """), {
            "id": source_id, "link_status": link_status, "checked_on": checked_on,
            "set_broken": set_broken, "updated_by": updated_by,
        })
        row = result.first()
        return dict(row._mapping) if row else None


@asynccontextmanager
async def open_repository() -> AsyncIterator[MedicalSourceRepository]:
    """A repository inside one transaction: committed when the block ends, rolled back on any error.
    Tests replace this function with an in-memory fake (tests/test_medical_sources_api.py)."""
    async with get_session_factory()() as session:
        async with session.begin():
            yield MedicalSourceRepository(session)


def database_url():
    """DATABASE_URL if set (the same URL Alembic and the seed script read), otherwise the DB_* values."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        return build_url()
    # asyncpg needs its own driver name in the URL; Alembic's plain postgresql:// or +psycopg URL is converted.
    for prefix in ("postgresql+psycopg://", "postgresql+psycopg2://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix):]
    return url


@asynccontextmanager
async def open_system_session() -> AsyncIterator[async_sessionmaker]:
    """A session factory for a one-off script such as a cron job (not the API's shared pool).
    The engine is closed when the block ends. Each `async with factory() as session, session.begin():`
    is one transaction."""
    engine = create_async_engine(database_url(), connect_args={"timeout": 10})
    try:
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


# ---------- read by the motors ----------

LIBRARY_COLUMNS = "id, country, language, organisation, authors, title, year, publication, doi, link, " \
                  "topics, motors, patient_label, evidence_level, status"


def read_all_sources_sync() -> List[dict]:
    """Every row of ai.medical_sources, any status, for library.py. Raises when the database cannot be reached."""
    import psycopg  # sync driver (already used by Alembic); the motors' routes are sync

    with psycopg.connect(
        host=config.DB_HOST, port=config.DB_PORT, dbname=config.DB_NAME,
        user=config.DB_APP_USER, password=config.DB_APP_PASSWORD,
        connect_timeout=1,  # read inside the motor's time budget: a down database must not use it up
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT {LIBRARY_COLUMNS} FROM ai.medical_sources ORDER BY id")
            names = [column.name for column in cursor.description]
            return [dict(zip(names, row)) for row in cursor.fetchall()]
