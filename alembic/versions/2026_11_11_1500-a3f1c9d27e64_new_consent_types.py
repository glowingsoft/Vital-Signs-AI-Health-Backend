"""new consent types (match the NestJS consent enum)

The allowed values of ai.consents.consent_type become the NestJS ConsentType enum:
  monitoring_fda, research_participation, image_processing, voice_recording,
  parental_consent, age_13_reconsent, adult_terms, triage
The old names (monitoring, research, camera_image, mexico_data_transfer) are no longer accepted.
Rows with an old name block the upgrade (the table is append-only, so they cannot be renamed).

Revision ID: a3f1c9d27e64
Revises: 7635008277b8
Create Date: 2026-11-11 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = 'a3f1c9d27e64'
down_revision: Union[str, Sequence[str], None] = '7635008277b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONSTRAINT = "consents_consent_type_check"
NEW_TYPES = ("monitoring_fda", "research_participation", "image_processing", "voice_recording",
             "parental_consent", "age_13_reconsent", "adult_terms", "triage")
OLD_TYPES = ("monitoring", "research", "voice_recording", "camera_image", "mexico_data_transfer")
NEW_COMMENT = ("monitoring_fda (24/7 monitoring with the FDA disclosure, required), research_participation (optional, revocable), "
               "image_processing (photo analysis), voice_recording (Spec, Addendum A), parental_consent (COPPA, under 13), "
               "age_13_reconsent (re-consent at 13), adult_terms (adult terms of use), triage (symptom triage).")
OLD_COMMENT = ("monitoring (required before monitoring starts), research (optional, revocable), voice_recording (Spec, Addendum A), "
               "camera_image (Spec, Motor 2), mexico_data_transfer (Proposal Week 4).")


def sql_list(values) -> str:
    return ", ".join(f"'{value}'" for value in values)


def switch(to_types, from_types, comment) -> None:
    rows = op.get_bind().execute(
        text(f"SELECT consent_type, count(*) FROM ai.consents WHERE consent_type NOT IN ({sql_list(to_types)}) GROUP BY 1")
    ).all()
    if rows:
        found = ", ".join(f"{name} ({count} rows)" for name, count in rows)
        raise RuntimeError(f"ai.consents has rows with types that would no longer be allowed: {found}.")
    op.drop_constraint(CONSTRAINT, "consents", schema="ai", type_="check")
    op.create_check_constraint(CONSTRAINT, "consents", f"consent_type IN ({sql_list(to_types)})", schema="ai")
    op.execute(f"COMMENT ON COLUMN ai.consents.consent_type IS '{comment}'")


def upgrade() -> None:
    switch(NEW_TYPES, OLD_TYPES, NEW_COMMENT)


def downgrade() -> None:
    switch(OLD_TYPES, NEW_TYPES, OLD_COMMENT)
