"""node_backend grants for the review endpoints

The NestJS review endpoints (patient response, doctor/supervisor confirm or correct, assessment history)
need, on top of what 7635008277b8 gave node_backend:
  SELECT on ai.assessments  - list a patient's assessments, check who owns one, read its values
  SELECT + INSERT on ai.reviews - save a response or review; read the newest one (also needed for RETURNING)
  INSERT on ai.audit_log    - the clinical audit trail row written with each review. Insert only: NestJS
                              never reads the audit trail, so its INSERT must not use RETURNING.
ai.reviews and ai.audit_log are append-only (their triggers block UPDATE, DELETE and TRUNCATE for every login),
so node_backend gets no UPDATE or DELETE anywhere.

Runs only when the node_backend login exists (it is created by 7635008277b8 when NODE_BACKEND_PASSWORD is set);
on a Mac without it this migration only records that it ran. GRANT and REVOKE are safe to repeat.
Note: running 7635008277b8 again revokes everything first, so always finish with `alembic upgrade head`.

Revision ID: b7d2e4f10a35
Revises: a3f1c9d27e64
Create Date: 2026-11-11 16:00:00.000000

"""
import logging
import os
import re
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = 'b7d2e4f10a35'
down_revision: Union[str, Sequence[str], None] = 'a3f1c9d27e64'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

# The NestJS backend's login: DB_NODE_USER from .env (the same login postgres/bootstrap.sql creates),
# "node_backend" when it is not set. Only plain names are accepted, because the name is put into GRANT statements.
NODE_ROLE = os.environ.get("DB_NODE_USER") or "node_backend"
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", NODE_ROLE):
    raise ValueError(f"DB_NODE_USER must be a plain lowercase Postgres name, got {NODE_ROLE!r}")


def role_exists() -> bool:
    return op.get_bind().execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": NODE_ROLE}).first() is not None


def upgrade() -> None:
    if not role_exists():
        logger.info("%s login does not exist: no review grants (fine for local development).", NODE_ROLE)
        return
    op.execute(f"GRANT SELECT ON ai.assessments TO {NODE_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON ai.reviews TO {NODE_ROLE}")
    op.execute(f"GRANT INSERT ON ai.audit_log TO {NODE_ROLE}")
    logger.info("%s: SELECT on ai.assessments, SELECT+INSERT on ai.reviews, INSERT on ai.audit_log.", NODE_ROLE)


def downgrade() -> None:
    if not role_exists():
        return
    # Back to what 7635008277b8 gives: ai.consents and ai.disclaimers only.
    op.execute(f"REVOKE ALL ON ai.assessments, ai.reviews, ai.audit_log FROM {NODE_ROLE}")
