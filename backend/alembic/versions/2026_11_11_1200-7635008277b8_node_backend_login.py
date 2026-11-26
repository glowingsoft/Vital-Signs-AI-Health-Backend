"""node_backend login for the NestJS backend

The NestJS backend connects over the private AWS network (SSL, see docker/postgres/pg_hba.conf) as
node_backend. It gets only what it needs, and nothing on tables added later:
  CONNECT on the database, USAGE on schema ai,
  SELECT + INSERT on ai.consents (append-only: its triggers block UPDATE, DELETE and TRUNCATE for every login),
  SELECT on ai.disclaimers.

Creating a login needs an admin: the app login that runs the migrations owns the tables but cannot create
roles. So the login is created (or its password updated) over a short admin connection (DB_ADMIN_USER /
DB_ADMIN_PASSWORD); the grants are made by the app login, which owns the tables.

Runs only when NODE_BACKEND_PASSWORD is set. On the server docker-compose.aws.yml requires it, so a deploy
cannot skip the login; on a Mac it is usually unset and this migration only records that it ran.
To run it again (for example after setting the password locally): alembic downgrade 0b79e3a3c8d8 && alembic upgrade head.
Every statement is safe to repeat: the login is created or updated, GRANT and REVOKE are idempotent.

Revision ID: 7635008277b8
Revises: 0b79e3a3c8d8
Create Date: 2026-11-11 12:00:00.000000

"""
import logging
import os
import re
from typing import Sequence, Union

from alembic import op
from sqlalchemy import create_engine, pool, text

# revision identifiers, used by Alembic.
revision: str = '7635008277b8'
down_revision: Union[str, Sequence[str], None] = '0b79e3a3c8d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

# The NestJS backend's login: DB_NODE_USER from .env (the same login postgres/bootstrap.sql creates),
# "node_backend" when it is not set. Only plain names are accepted, because the name is put into GRANT statements.
NODE_ROLE = os.environ.get("DB_NODE_USER") or "node_backend"
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", NODE_ROLE):
    raise ValueError(f"DB_NODE_USER must be a plain lowercase Postgres name, got {NODE_ROLE!r}")


def admin_engine():
    """An admin connection to the same server and database the migration runs on."""
    url = op.get_bind().engine.url.set(
        username=os.environ.get("DB_ADMIN_USER") or "postgres",
        password=os.environ.get("DB_ADMIN_PASSWORD") or None,
    )
    return create_engine(url, poolclass=pool.NullPool)


def create_login(password: str) -> None:
    """Create the login (or update its password) over a short admin connection."""
    # The password travels as a bound parameter into a transaction-local setting, so it is never part of the SQL text.
    try:
        with admin_engine().begin() as admin:
            admin.execute(text("SELECT set_config('vitalsigns.node_backend_password', :p, true)"), {"p": password})
            admin.execute(text(f"""
                DO $$ BEGIN
                    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{NODE_ROLE}') THEN
                        EXECUTE format('ALTER ROLE {NODE_ROLE} LOGIN PASSWORD %L', current_setting('vitalsigns.node_backend_password'));
                    ELSE
                        EXECUTE format('CREATE ROLE {NODE_ROLE} LOGIN PASSWORD %L', current_setting('vitalsigns.node_backend_password'));
                    END IF;
                END $$
            """))
            # Least privilege, also when the login already existed with more.
            admin.execute(text(
                f"ALTER ROLE {NODE_ROLE} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT "
                "CONNECTION LIMIT 20"
            ))
    except Exception as error:
        raise RuntimeError(
            f"The admin login could not create {NODE_ROLE}. "
            "Check DB_ADMIN_USER and DB_ADMIN_PASSWORD (the postgres admin password)."
        ) from error


def role_exists() -> bool:
    return op.get_bind().execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": NODE_ROLE}).first() is not None


def upgrade() -> None:
    # postgres/bootstrap.sql (local Docker, setup_db.sh) already creates the login from DB_NODE_USER /
    # DB_NODE_PASSWORD. Only when it does not exist yet is it created here, over an admin connection.
    if not role_exists():
        password = os.environ.get("NODE_BACKEND_PASSWORD") or os.environ.get("DB_NODE_PASSWORD")
        if not password:
            logger.info("%s login does not exist and no password is set: not created (fine for local development).",
                        NODE_ROLE)
            return
        create_login(password)

    # ---------- The grants (app login, which owns the database, the schema and the tables) ----------
    # CONNECT is needed too: the first migration took it away from PUBLIC.
    op.execute(f"""
        DO $$ BEGIN
            EXECUTE format('GRANT CONNECT ON DATABASE %I TO {NODE_ROLE}', current_database());
        END $$
    """)
    # Nothing else anywhere in ai, even if it was granted by hand before.
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA ai FROM {NODE_ROLE}")
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA ai FROM {NODE_ROLE}")
    op.execute(f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA ai FROM {NODE_ROLE}")
    op.execute(f"REVOKE ALL ON SCHEMA ai FROM {NODE_ROLE}")
    op.execute(f"GRANT USAGE ON SCHEMA ai TO {NODE_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON ai.consents TO {NODE_ROLE}")
    op.execute(f"GRANT SELECT ON ai.disclaimers TO {NODE_ROLE}")
    logger.info("%s: login ready; SELECT+INSERT on ai.consents, SELECT on ai.disclaimers.", NODE_ROLE)


def downgrade() -> None:
    if not role_exists():
        return
    # Everything on the admin connection: grants removed in this migration's own transaction would not be
    # committed yet, so DROP ROLE on another connection would still see them.
    # DROP OWNED revokes every privilege of the login in this database (it owns nothing).
    try:
        with admin_engine().begin() as admin:
            admin.execute(text(f"DROP OWNED BY {NODE_ROLE}"))
            admin.execute(text(f"DROP ROLE {NODE_ROLE}"))
        return
    except Exception as error:
        logger.warning("%s: no admin connection, so the login stays; removing its access instead (%s).", NODE_ROLE, error)
    op.execute(f"REVOKE ALL ON ai.consents, ai.disclaimers FROM {NODE_ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA ai FROM {NODE_ROLE}")
    op.execute(f"""
        DO $$ BEGIN
            EXECUTE format('REVOKE CONNECT ON DATABASE %I FROM {NODE_ROLE}', current_database());
        END $$
    """)
