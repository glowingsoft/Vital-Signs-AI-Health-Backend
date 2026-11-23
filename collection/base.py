# The shared SQLAlchemy Base for every model in collection/, plus small helpers the models reuse.
# All tables live in the "ai" Postgres schema, so it is set once here on the metadata.
# Alembic compares these models with the database to write migrations (see alembic/env.py).

from typing import Sequence

from sqlalchemy import DateTime, MetaData
from sqlalchemy.orm import DeclarativeBase

SCHEMA = "ai"


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA)


# timestamptz in Postgres.
TIMESTAMPTZ = DateTime(timezone=True)

# Allowed values, shared by several tables' CHECK constraints.
RISK_LEVELS = ("low", "medium", "high", "critical")
LABELS = ("confirmed_diagnosis", "reported_symptom", "normal")
LABEL_SOURCES = ("user", "labeler", "supervisor", "doctor", "system")
MOTORS = ("triage", "visual", "vitals")
# ai.motor_results.source_status: did the AI's source id match the trusted library?
SOURCE_MATCH_STATUSES = ("matched", "pending_medical_review")


def one_of(column: str, values: Sequence[str]) -> str:
    """SQL for a CHECK constraint: one_of("motor", MOTORS) -> "motor IN ('triage', 'visual', 'vitals')"."""
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"
