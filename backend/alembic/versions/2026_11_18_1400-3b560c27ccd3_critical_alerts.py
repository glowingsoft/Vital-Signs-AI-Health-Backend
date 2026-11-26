"""critical alerts log

Adds ai.critical_alerts: one row per full-screen critical alert shown on the phone, with the time it was shown,
what triggered it, what the user did and how long it took to dismiss (Spec, Critical alert (SOS)).
The NestJS backend writes the rows (POST /sos/alerts); alerts raised offline arrive in a batch when the phone
syncs, and UNIQUE (user_id, client_alert_id) keeps a re-sent alert from being saved twice.

trigger_vital uses the column names of ai.vitals_readings (heart_rate, spo2, systolic_bp, diastolic_bp,
respiratory_rate). Append-only, like ai.audit_log and ai.consents: ai.block_changes() blocks UPDATE, DELETE and
TRUNCATE for every login.

node_backend gets, when that login exists:
  INSERT on ai.critical_alerts                        - save the alerts
  SELECT (user_id, client_alert_id) on ai.critical_alerts
                                                      - only for the duplicate check (ON CONFLICT and RETURNING
                                                        client_alert_id). No other column can be read.
INSERT on ai.audit_log was already given by b7d2e4f10a35. node_backend gets no UPDATE, DELETE or TRUNCATE.
Running 7635008277b8 again revokes everything, so finish with `alembic upgrade head`.

Revision ID: 3b560c27ccd3
Revises: e8b4f2a6c731
Create Date: 2026-11-18 14:00:00.000000

"""
import logging
import os
import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '3b560c27ccd3'
down_revision: Union[str, Sequence[str], None] = 'e8b4f2a6c731'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

# The NestJS backend's login: DB_NODE_USER from .env (the same login postgres/bootstrap.sql creates),
# "node_backend" when it is not set. Only plain names are accepted, because the name is put into GRANT statements.
NODE_ROLE = os.environ.get("DB_NODE_USER") or "node_backend"
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", NODE_ROLE):
    raise ValueError(f"DB_NODE_USER must be a plain lowercase Postgres name, got {NODE_ROLE!r}")
TABLE = "critical_alerts"


def role_exists() -> bool:
    return op.get_bind().execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": NODE_ROLE}).first() is not None


def upgrade() -> None:
    op.create_table(
        'critical_alerts',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False, comment='Pseudonymous ID only (Spec, Privacy). Same as ai.vitals_readings.user_id.'),
        sa.Column('client_alert_id', sa.Text(), nullable=False,
                  comment='ID created on the phone. Unique per user, so an offline alert synced twice is saved once.'),
        sa.Column('shown_at', postgresql.TIMESTAMP(timezone=True), nullable=False,
                  comment='When the alert was shown on the phone (Spec, Critical alert (SOS)).'),
        sa.Column('dismissed_at', postgresql.TIMESTAMP(timezone=True), nullable=True,
                  comment='When the user dismissed it. Empty when the app was closed first.'),
        sa.Column('time_to_dismiss_seconds', sa.Numeric(precision=10, scale=2), nullable=True,
                  comment='dismissed_at - shown_at, computed by the Node backend (Spec, Critical alert (SOS)).'),
        sa.Column('action', sa.Text(), nullable=False,
                  comment='What the user did: sos, call_911, dismissed or none (Spec, Critical alert (SOS)).'),
        sa.Column('trigger_vital', sa.Text(), nullable=False,
                  comment='The vital that raised the alert, named as in ai.vitals_readings: heart_rate, spo2, '
                          'systolic_bp, diastolic_bp, respiratory_rate.'),
        sa.Column('trigger_value', sa.Text(), nullable=False, comment='The value as shown, e.g. "165" or "185/95".'),
        sa.Column('trigger_source', sa.Text(), nullable=False,
                  comment='on_device (phone rules) or ai_assessment (result of POST /ai/assess).'),
        sa.Column('assessment_id', sa.UUID(), nullable=True, comment='The assessment, when the alert came from POST /ai/assess.'),
        sa.Column('recorded_offline', sa.Boolean(), server_default=sa.text('false'), nullable=False,
                  comment='True when raised with no internet and synced later.'),
        sa.Column('supervisor_notified', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('state', sa.Text(), nullable=True),
        sa.Column('country', sa.Text(), nullable=False),
        sa.Column('app_version', sa.Text(), nullable=True),
        sa.Column('device_platform', sa.Text(), nullable=True),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("action IN ('sos', 'call_911', 'dismissed', 'none')", name='critical_alerts_action_check'),
        sa.CheckConstraint("trigger_source IN ('on_device', 'ai_assessment')", name='critical_alerts_trigger_source_check'),
        sa.CheckConstraint("device_platform IN ('ios', 'android')", name='critical_alerts_device_platform_check'),
        sa.CheckConstraint('dismissed_at IS NULL OR dismissed_at >= shown_at', name='critical_alerts_dismissed_at_check'),
        sa.CheckConstraint('time_to_dismiss_seconds IS NULL OR time_to_dismiss_seconds >= 0',
                           name='critical_alerts_time_to_dismiss_seconds_check'),
        sa.ForeignKeyConstraint(['assessment_id'], ['ai.assessments.id'], name='critical_alerts_assessment_id_fkey'),
        sa.PrimaryKeyConstraint('id', name='critical_alerts_pkey'),
        sa.UniqueConstraint('user_id', 'client_alert_id', name='critical_alerts_user_id_client_alert_id_key'),
        schema='ai',
        comment='Every critical alert shown on the phone: when, what triggered it, what the user did and the time to '
                'dismiss (Spec, Critical alert (SOS)). Written by the Node backend (POST /sos/alerts). Append-only.',
    )
    op.create_index('critical_alerts_user_time_idx', 'critical_alerts',
                    ['user_id', sa.literal_column('shown_at DESC')], unique=False, schema='ai')
    op.create_index('critical_alerts_state_time_idx', 'critical_alerts',
                    ['state', sa.literal_column('shown_at DESC')], unique=False, schema='ai')

    # Append-only, the same triggers as the initial migration gives ai.audit_log and ai.consents.
    op.execute(f"""
        CREATE OR REPLACE TRIGGER {TABLE}_no_change
            BEFORE UPDATE OR DELETE ON ai.{TABLE}
            FOR EACH ROW EXECUTE FUNCTION ai.block_changes()
    """)
    op.execute(f"""
        CREATE OR REPLACE TRIGGER {TABLE}_no_truncate
            BEFORE TRUNCATE ON ai.{TABLE}
            FOR EACH STATEMENT EXECUTE FUNCTION ai.block_changes()
    """)

    if not role_exists():
        logger.info("%s login does not exist: no critical_alerts grants (fine for local development).", NODE_ROLE)
        return
    op.execute(f"GRANT INSERT ON ai.{TABLE} TO {NODE_ROLE}")
    op.execute(f"GRANT SELECT (user_id, client_alert_id) ON ai.{TABLE} TO {NODE_ROLE}")
    logger.info("%s: INSERT and SELECT (user_id, client_alert_id) on ai.%s.", NODE_ROLE, TABLE)


def downgrade() -> None:
    if role_exists():
        op.execute(f"REVOKE SELECT (user_id, client_alert_id) ON ai.{TABLE} FROM {NODE_ROLE}")
        op.execute(f"REVOKE INSERT ON ai.{TABLE} FROM {NODE_ROLE}")
    op.execute(f"DROP TRIGGER IF EXISTS {TABLE}_no_truncate ON ai.{TABLE}")
    op.execute(f"DROP TRIGGER IF EXISTS {TABLE}_no_change ON ai.{TABLE}")
    op.drop_index('critical_alerts_state_time_idx', table_name='critical_alerts', schema='ai')
    op.drop_index('critical_alerts_user_time_idx', table_name='critical_alerts', schema='ai')
    op.drop_table('critical_alerts', schema='ai')
