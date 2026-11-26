"""medical sources: approval and author columns for the admin API

From now on sources are added and changed only through /ai/admin/medical-sources (app/medical_sources/).
Adds to ai.medical_sources:
  approved_by, approved_at, approval_note   the client's written approval (needed for status 'active')
  created_by, updated_by                    the admin user id ('seed' for the first load)
and a CHECK that an active source always has its approval recorded.

Revision ID: e8b4f2a6c731
Revises: d5a7c3e91f42
Create Date: 2026-11-18 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e8b4f2a6c731'
down_revision: Union[str, Sequence[str], None] = 'd5a7c3e91f42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CHECK = "medical_sources_active_needs_approval_check"


def upgrade() -> None:
    op.add_column('medical_sources', sa.Column(
        'approved_by', sa.Text(), nullable=True, comment="Who gave the client's written approval."), schema='ai')
    op.add_column('medical_sources', sa.Column(
        'approved_at', postgresql.TIMESTAMP(timezone=True), nullable=True, comment='Date of the written approval.'),
        schema='ai')
    op.add_column('medical_sources', sa.Column(
        'approval_note', sa.Text(), nullable=True, comment='The approval message, or where it is.'), schema='ai')
    op.add_column('medical_sources', sa.Column(
        'created_by', sa.Text(), nullable=True, comment="Admin user id, or 'seed' for the first load."), schema='ai')
    op.add_column('medical_sources', sa.Column(
        'updated_by', sa.Text(), nullable=True, comment='Admin user id of the last change.'), schema='ai')
    op.execute("UPDATE ai.medical_sources SET created_by = 'seed', updated_by = 'seed' WHERE created_by IS NULL")
    # Fails if a source was set to 'active' by hand without an approval: record the approval first.
    op.create_check_constraint(
        CHECK, 'medical_sources',
        "status <> 'active' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL AND approval_note IS NOT NULL)",
        schema='ai',
    )


def downgrade() -> None:
    op.drop_constraint(CHECK, 'medical_sources', schema='ai', type_='check')
    for column in ('updated_by', 'created_by', 'approval_note', 'approved_at', 'approved_by'):
        op.drop_column('medical_sources', column, schema='ai')
