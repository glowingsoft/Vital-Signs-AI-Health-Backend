"""node_backend grants for the offline vitals sync

The NestJS endpoint POST /offline-vitals saves the readings a phone recorded without internet into
ai.vitals_readings (recorded_offline = true). It needs:
  INSERT on ai.vitals_readings                      - save the readings
  SELECT (user_id, client_reading_id) on ai.vitals_readings
                                                    - Postgres needs it for ON CONFLICT (user_id, client_reading_id),
                                                      which skips readings already saved, and for RETURNING
                                                      client_reading_id. The vital values themselves cannot be read.
INSERT on ai.audit_log was already given by b7d2e4f10a35. node_backend gets no UPDATE or DELETE.

Runs only when the node_backend login exists (created by 7635008277b8 when NODE_BACKEND_PASSWORD is set).
GRANT and REVOKE are safe to repeat. Running 7635008277b8 again revokes everything, so finish with `alembic upgrade head`.

Revision ID: c4e8a1f2b903
Revises: b7d2e4f10a35
Create Date: 2026-11-12 16:00:00.000000

"""
import logging
import os
import re
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = 'c4e8a1f2b903'
down_revision: Union[str, Sequence[str], None] = 'b7d2e4f10a35'
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
        logger.info("%s login does not exist: no offline vitals grants (fine for local development).", NODE_ROLE)
        return
    op.execute(f"GRANT INSERT ON ai.vitals_readings TO {NODE_ROLE}")
    op.execute(f"GRANT SELECT (user_id, client_reading_id) ON ai.vitals_readings TO {NODE_ROLE}")
    logger.info("%s: INSERT and SELECT (user_id, client_reading_id) on ai.vitals_readings.", NODE_ROLE)


def downgrade() -> None:
    if not role_exists():
        return
    op.execute(f"REVOKE SELECT (user_id, client_reading_id) ON ai.vitals_readings FROM {NODE_ROLE}")
    op.execute(f"REVOKE INSERT ON ai.vitals_readings FROM {NODE_ROLE}")
