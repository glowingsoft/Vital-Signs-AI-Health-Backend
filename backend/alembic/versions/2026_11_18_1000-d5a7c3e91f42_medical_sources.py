"""trusted medical source library

Adds ai.medical_sources (the closed list of sources the AI may cite, loaded from
db/medical_sources/medical_sources.json by scripts/load_medical_sources.py), and two columns on ai.motor_results:
  source_id      the library id the AI picked and the backend confirmed (empty when none matched)
  source_status  matched, or pending_medical_review

source_id has no foreign key on purpose: a result is still saved if the library row is missing.
node_backend gets SELECT on ai.medical_sources (doctors see the full citation), when that login exists.

Revision ID: d5a7c3e91f42
Revises: c4e8a1f2b903
Create Date: 2026-11-18 10:00:00.000000

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
revision: str = 'd5a7c3e91f42'
down_revision: Union[str, Sequence[str], None] = 'c4e8a1f2b903'
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
    op.create_table(
        'medical_sources',
        sa.Column('id', sa.Text(), nullable=False, comment='Stable id the AI returns, e.g. US-AHA-HBP-2025.'),
        sa.Column('country', sa.Text(), nullable=False),
        sa.Column('language', sa.Text(), nullable=False),
        sa.Column('organisation', sa.Text(), nullable=False),
        sa.Column('authors', sa.Text(), nullable=True),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('year', sa.Integer(), nullable=True),
        sa.Column('publication', sa.Text(), nullable=True),
        sa.Column('doi', sa.Text(), nullable=True),
        sa.Column('link', sa.Text(), nullable=False),
        sa.Column('topics', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column('motors', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False,
                  comment='triage, visual, vitals'),
        sa.Column('patient_label', sa.Text(), nullable=False, comment='Short line shown to the patient.'),
        sa.Column('evidence_level', sa.Text(), server_default=sa.text("'clinical_guideline'"), nullable=False),
        sa.Column('status', sa.Text(), server_default=sa.text("'draft'"), nullable=False),
        sa.Column('client_decision', sa.Text(), nullable=True, comment="Decision in the client's review sheet."),
        sa.Column('needs_client_confirmation', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('current_note', sa.Text(), nullable=True, comment='Whether this is the latest version.'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('link_status', sa.Text(), nullable=True, comment='Result of the last link check: ok or broken.'),
        sa.Column('link_checked_at', sa.Date(), nullable=True),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("country IN ('US', 'MX')", name='medical_sources_country_check'),
        sa.CheckConstraint(
            "status IN ('draft', 'proposed_addition', 'active', 'inactive', 'broken')", name='medical_sources_status_check'
        ),
        sa.CheckConstraint("motors <@ ARRAY['triage', 'visual', 'vitals']::text[]", name='medical_sources_motors_check'),
        sa.CheckConstraint("link LIKE 'https://%'", name='medical_sources_link_check'),
        sa.PrimaryKeyConstraint('id'),
        schema='ai',
        comment="Trusted medical source library (Addendum A, medical-source field). Only status 'active' is given to "
                "the AI. Never deleted: set status to 'inactive' so past results keep their source.",
    )

    op.add_column('motor_results', sa.Column(
        'source_id', sa.Text(), nullable=True,
        comment='ai.medical_sources.id the AI picked and the backend confirmed. Empty when no source matched.',
    ), schema='ai')
    op.add_column('motor_results', sa.Column(
        'source_status', sa.Text(), nullable=True,
        comment='matched, or pending_medical_review when the AI gave no source or one not in the library.',
    ), schema='ai')
    op.create_check_constraint(
        'motor_results_source_status_check', 'motor_results',
        "source_status IN ('matched', 'pending_medical_review')", schema='ai',
    )

    if role_exists():
        op.execute(f"GRANT SELECT ON ai.medical_sources TO {NODE_ROLE}")
        logger.info("%s: SELECT on ai.medical_sources.", NODE_ROLE)
    else:
        logger.info("%s login does not exist: no medical_sources grant (fine for local development).", NODE_ROLE)


def downgrade() -> None:
    op.drop_constraint('motor_results_source_status_check', 'motor_results', schema='ai', type_='check')
    op.drop_column('motor_results', 'source_status', schema='ai')
    op.drop_column('motor_results', 'source_id', schema='ai')
    op.drop_table('medical_sources', schema='ai')  # also removes its grants
