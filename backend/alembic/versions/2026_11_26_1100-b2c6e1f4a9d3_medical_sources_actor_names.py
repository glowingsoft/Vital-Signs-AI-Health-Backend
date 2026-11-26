"""medical sources: names of who created and last changed a source

created_by / updated_by hold the admin's user id (a MongoDB ObjectId from the NestJS token), which means nothing
to a person reading the admin screen. These columns keep the admin's name from the same token next to the id,
so the API can show "Updated by: Jane Admin" while the id stays for the audit trail.
Rows from the first load ('seed') get the name 'System (seed)'. Older admin rows keep an empty name until the
next change; the API then shows the id.

Revision ID: b2c6e1f4a9d3
Revises: f3a9c2d81b40
Create Date: 2026-11-26 11:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b2c6e1f4a9d3'
down_revision: Union[str, Sequence[str], None] = 'f3a9c2d81b40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('medical_sources', sa.Column(
        'created_by_name', sa.Text(), nullable=True, comment='Name of the admin who added it (from the token).'),
        schema='ai')
    op.add_column('medical_sources', sa.Column(
        'updated_by_name', sa.Text(), nullable=True, comment='Name of the admin who changed it last (from the token).'),
        schema='ai')
    op.execute("UPDATE ai.medical_sources SET created_by_name = 'System (seed)' WHERE created_by = 'seed'")
    op.execute("UPDATE ai.medical_sources SET updated_by_name = 'System (seed)' WHERE updated_by = 'seed'")
    # The list is sorted newest first (GET /ai/admin/medical-sources).
    op.create_index('medical_sources_created_at_idx', 'medical_sources', [sa.literal_column('created_at DESC')],
                    schema='ai')


def downgrade() -> None:
    op.drop_index('medical_sources_created_at_idx', table_name='medical_sources', schema='ai')
    op.drop_column('medical_sources', 'updated_by_name', schema='ai')
    op.drop_column('medical_sources', 'created_by_name', schema='ai')
