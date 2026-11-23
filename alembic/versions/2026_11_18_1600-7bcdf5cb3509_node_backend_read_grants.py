"""node_backend read grants for doctor and supervisor views

Doctors, supervisors and admins see these tables in the Node app. node_backend gets, when that login exists:
  SELECT on ai.motor_results    - the doctor sees each AI service's answer and its source: finding_type,
                                  evidence_level, reference (full citation), source_id, source_status, model_name
  SELECT on ai.vitals_readings  - doctor and supervisor see the patient's readings, labels and the 7/30-day trends
  SELECT on ai.critical_alerts  - doctor and supervisor see the patient's SOS alerts: when, trigger, action,
                                  time to dismiss
The table-level SELECT covers the column-level SELECTs that c4e8a1f2b903 (user_id, client_reading_id) and
3b560c27ccd3 (user_id, client_alert_id) gave; those stay as they are.

Not granted: ai.ai_usage (internal cost data, not patient care) and SELECT on ai.audit_log (it stays write-only
for Node). node_backend gets no UPDATE, DELETE or TRUNCATE on any table.

The database only allows the read: the Node backend still decides who may see which patient, and writes an
ai.audit_log "view" row for every view of health data.

Runs only when the node_backend login exists (created by 7635008277b8 when NODE_BACKEND_PASSWORD is set).
GRANT and REVOKE are safe to repeat. Running 7635008277b8 again revokes everything, so finish with `alembic upgrade head`.

Revision ID: 7bcdf5cb3509
Revises: 3b560c27ccd3
Create Date: 2026-11-18 16:00:00.000000

"""
import logging
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = '7bcdf5cb3509'
down_revision: Union[str, Sequence[str], None] = '3b560c27ccd3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

NODE_ROLE = "node_backend"  # same login as 7635008277b8
READ_TABLES = ["motor_results", "vitals_readings", "critical_alerts"]
# Column-level SELECTs from earlier migrations. REVOKE SELECT on a table also removes them, so downgrade gives them back.
COLUMN_SELECTS = {
    "vitals_readings": "user_id, client_reading_id",  # c4e8a1f2b903
    "critical_alerts": "user_id, client_alert_id",  # 3b560c27ccd3
}


def role_exists() -> bool:
    return op.get_bind().execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": NODE_ROLE}).first() is not None


def upgrade() -> None:
    if not role_exists():
        logger.info("%s login does not exist: no read grants (fine for local development).", NODE_ROLE)
        return
    for table in READ_TABLES:
        op.execute(f"GRANT SELECT ON ai.{table} TO {NODE_ROLE}")
    logger.info("%s: SELECT on %s.", NODE_ROLE, ", ".join(f"ai.{table}" for table in READ_TABLES))


def downgrade() -> None:
    if not role_exists():
        return
    for table in READ_TABLES:
        op.execute(f"REVOKE SELECT ON ai.{table} FROM {NODE_ROLE}")
    for table, columns in COLUMN_SELECTS.items():
        op.execute(f"GRANT SELECT ({columns}) ON ai.{table} TO {NODE_ROLE}")
