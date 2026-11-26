"""repair: node grants and consent types on databases migrated by the broken v2_main_new chain

The v2_main_new branch lost six migrations (7635008277b8, a3f1c9d27e64, b7d2e4f10a35, c4e8a1f2b903,
3b560c27ccd3, 7bcdf5cb3509) and pointed d5a7c3e91f42 straight at the initial schema. A database upgraded with
that branch is stamped e8b4f2a6c731 but never got what those migrations do. With the chain restored, Alembic
only runs what comes after e8b4f2a6c731, so this migration makes the result the same on every database:

  ai.consents.consent_type  allows the NestJS ConsentType values (a3f1c9d27e64). Old values are kept allowed
                            only if rows with them exist (the table is append-only, they cannot be renamed).
  DB_NODE_USER grants       exactly what the NestJS backend needs (7635008277b8, b7d2e4f10a35, c4e8a1f2b903,
                            3b560c27ccd3, 7bcdf5cb3509, d5a7c3e91f42). GRANT is idempotent, so on a database
                            that already has them nothing changes.

Safe on every database, also a new empty one (then it changes nothing).

Revision ID: f3a9c2d81b40
Revises: 7bcdf5cb3509
Create Date: 2026-11-26 10:00:00.000000

"""
import logging
import os
import re
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = 'f3a9c2d81b40'
down_revision: Union[str, Sequence[str], None] = '7bcdf5cb3509'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

# Same rule as the other node migrations: DB_NODE_USER, else "node_backend".
NODE_ROLE = os.environ.get("DB_NODE_USER") or "node_backend"
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", NODE_ROLE):
    raise ValueError(f"DB_NODE_USER must be a plain lowercase Postgres name, got {NODE_ROLE!r}")

CONSTRAINT = "consents_consent_type_check"
NEW_TYPES = (
    "monitoring_fda", "research_participation", "image_processing", "voice_recording",
    "parental_consent", "age_13_reconsent", "adult_terms", "triage",
)

NEW_COMMENT = ("monitoring_fda (24/7 monitoring with the FDA disclosure, required), research_participation (optional, revocable), "
               "image_processing (photo analysis), voice_recording (Spec, Addendum A), parental_consent (COPPA, under 13), "
               "age_13_reconsent (re-consent at 13), adult_terms (adult terms of use), triage (symptom triage).")

# What the NestJS backend may do, in one place (each line says which migration first gave it).
NODE_GRANTS = [
    "GRANT USAGE ON SCHEMA ai TO {role}",                                            # 7635008277b8
    "GRANT SELECT, INSERT ON ai.consents TO {role}",                                 # 7635008277b8
    "GRANT SELECT ON ai.disclaimers TO {role}",                                      # 7635008277b8
    "GRANT SELECT ON ai.assessments TO {role}",                                      # b7d2e4f10a35
    "GRANT SELECT, INSERT ON ai.reviews TO {role}",                                  # b7d2e4f10a35
    "GRANT INSERT ON ai.audit_log TO {role}",                                        # b7d2e4f10a35
    "GRANT SELECT, INSERT ON ai.vitals_readings TO {role}",                          # c4e8a1f2b903 + 7bcdf5cb3509
    "GRANT SELECT ON ai.medical_sources TO {role}",                                  # d5a7c3e91f42
    "GRANT SELECT, INSERT ON ai.critical_alerts TO {role}",                          # 3b560c27ccd3 + 7bcdf5cb3509
    "GRANT SELECT ON ai.motor_results TO {role}",                                    # 7bcdf5cb3509
]


def sql_list(values) -> str:
    return ", ".join(f"'{value}'" for value in values)


def role_exists() -> bool:
    return op.get_bind().execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": NODE_ROLE}).first() is not None


def fix_consent_types() -> None:
    bind = op.get_bind()
    definition = bind.execute(text(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = :c AND conrelid = 'ai.consents'::regclass"
    ), {"c": CONSTRAINT}).scalar()
    if definition and all(f"'{value}'" in definition for value in NEW_TYPES):
        return  # a3f1c9d27e64 already ran here
    old_rows = [row[0] for row in bind.execute(text(
        f"SELECT DISTINCT consent_type FROM ai.consents WHERE consent_type NOT IN ({sql_list(NEW_TYPES)})"
    ))]
    if old_rows:
        logger.warning("ai.consents has rows with old consent types %s: they stay allowed next to the new ones.", old_rows)
    if definition:
        op.drop_constraint(CONSTRAINT, "consents", schema="ai", type_="check")
    op.create_check_constraint(
        CONSTRAINT, "consents", f"consent_type IN ({sql_list(NEW_TYPES + tuple(old_rows))})", schema="ai",
    )
    op.execute(f"COMMENT ON COLUMN ai.consents.consent_type IS '{NEW_COMMENT}'")
    logger.info("ai.consents: consent_type now allows the NestJS ConsentType values.")


def upgrade() -> None:
    fix_consent_types()

    if not role_exists():
        logger.info("%s login does not exist: no node grants (fine for local development).", NODE_ROLE)
        return
    op.execute(f"""
        DO $$ BEGIN
            EXECUTE format('GRANT CONNECT ON DATABASE %I TO {NODE_ROLE}', current_database());
        END $$
    """)
    # Start from nothing (like 7635008277b8), so a login that still has the initial schema's broad SELECT
    # (ai_usage, audit_log, alembic_version) ends up with exactly the list below on every database.
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA ai FROM {NODE_ROLE}")
    for statement in NODE_GRANTS:
        op.execute(statement.format(role=NODE_ROLE))
    logger.info("%s: every NestJS grant is in place.", NODE_ROLE)


def downgrade() -> None:
    # Nothing to undo: every grant and the constraint belong to the earlier migrations listed above.
    pass
