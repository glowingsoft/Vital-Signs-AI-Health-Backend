# Tests for ai.critical_alerts (migration 3b560c27ccd3, collection/critical_alert.py).
#
# - The model and the migration: always run.
# - The table's rules (unique alert per user, append-only, CHECK constraints): need a migrated Postgres, from
#   DATABASE_URL or the DB_* values (as Alembic). Skipped when it cannot be reached or has no ai.critical_alerts.
#   Every insert is rolled back, so no row is left behind (the table is append-only).
# - The migration on an empty database and on the current head: opt-in, because it creates and drops a scratch
#   database. Set MIGRATION_TEST_ADMIN_URL to an admin login on the same server, e.g. for Docker:
#     MIGRATION_TEST_ADMIN_URL=postgresql://postgres:<password>@localhost:5433/postgres
#   The scratch database is owned by the app login (DB_APP_USER), like the real one.
# Run with: pytest

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import URL, make_url

from collection import Base, CriticalAlert

ROOT = Path(__file__).resolve().parent.parent
REVISION = "3b560c27ccd3"
PREVIOUS = "e8b4f2a6c731"


# ---------- the model and the migration file ----------

def test_revision_follows_the_previous_head():
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert scripts.get_revision(REVISION).down_revision == PREVIOUS


def test_model_columns_match_the_spec():
    table = Base.metadata.tables["ai.critical_alerts"]
    required = {
        "id", "user_id", "client_alert_id", "shown_at", "action", "trigger_vital", "trigger_value", "trigger_source",
        "recorded_offline", "supervisor_notified", "country", "created_at",
    }
    optional = {"dismissed_at", "time_to_dismiss_seconds", "assessment_id", "state", "app_version", "device_platform"}
    assert set(table.columns.keys()) == required | optional
    assert {name for name, column in table.columns.items() if not column.nullable} == required
    assert [fk.target_fullname for fk in table.c.assessment_id.foreign_keys] == ["ai.assessments.id"]
    assert (table.c.time_to_dismiss_seconds.type.precision, table.c.time_to_dismiss_seconds.type.scale) == (10, 2)
    assert CriticalAlert.__table__ is table


def test_trigger_vital_names_are_the_vitals_readings_columns():
    # trigger_vital uses the column names of ai.vitals_readings (systolic_bp, not systolic).
    readings = Base.metadata.tables["ai.vitals_readings"].columns.keys()
    for vital in ["heart_rate", "spo2", "systolic_bp", "diastolic_bp", "respiratory_rate"]:
        assert vital in readings
        assert vital in Base.metadata.tables["ai.critical_alerts"].c.trigger_vital.comment


# ---------- the table's rules, on a migrated database ----------

def database_url() -> URL:
    """Same as alembic/env.py, read from the environment (tests patch config.DB_NAME)."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return make_url(url).set(drivername="postgresql")
    return URL.create(
        "postgresql", username=os.environ.get("DB_APP_USER"), password=os.environ.get("DB_APP_PASSWORD"),
        host=os.environ.get("DB_HOST") or "localhost", port=int(os.environ.get("DB_PORT") or "5432"),
        database=os.environ.get("DB_NAME"),
    )


def plain(url: URL) -> str:
    return url.render_as_string(hide_password=False)


@pytest.fixture
def cursor():
    """A cursor inside a transaction that is always rolled back."""
    psycopg = pytest.importorskip("psycopg")
    if not os.environ.get("DATABASE_URL") and not os.environ.get("DB_NAME"):
        pytest.skip("no database configured")
    try:
        connection = psycopg.connect(plain(database_url()), connect_timeout=2)
    except Exception as error:
        pytest.skip(f"Postgres is not reachable: {error}")
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT to_regclass('ai.critical_alerts')")
            if cur.fetchone()[0] is None:
                pytest.skip("ai.critical_alerts does not exist: run alembic upgrade head")
            yield cur
    finally:
        connection.rollback()
        connection.close()


def alert(**values) -> dict:
    row = {
        "user_id": "test_alert_user", "client_alert_id": f"a_{uuid.uuid4().hex[:8]}",
        "shown_at": "2026-11-18T10:00:00Z", "dismissed_at": "2026-11-18T10:00:12Z", "time_to_dismiss_seconds": 12,
        "action": "dismissed", "trigger_vital": "heart_rate", "trigger_value": "165", "trigger_source": "on_device",
        "country": "US",
    }
    row.update(values)
    return row


def insert(cur, row: dict):
    columns = ", ".join(row)
    cur.execute(
        f"INSERT INTO ai.critical_alerts ({columns}) VALUES ({', '.join('%(' + c + ')s' for c in row)}) RETURNING id",
        row,
    )
    return cur.fetchone()[0]


def refused(cur, sql_or_row, params=None) -> str:
    """Run it inside a savepoint and return the error message (empty when Postgres accepted it)."""
    cur.execute("SAVEPOINT attempt")
    try:
        if isinstance(sql_or_row, dict):
            insert(cur, sql_or_row)
        else:
            cur.execute(sql_or_row, params)
    except Exception as error:
        cur.execute("ROLLBACK TO SAVEPOINT attempt")
        return str(error)
    cur.execute("RELEASE SAVEPOINT attempt")
    return ""


def test_valid_alert_is_saved_with_its_defaults(cursor):
    alert_id = insert(cursor, alert(dismissed_at=None, time_to_dismiss_seconds=None, action="none"))
    cursor.execute(
        "SELECT recorded_offline, supervisor_notified, created_at IS NOT NULL FROM ai.critical_alerts WHERE id = %s",
        (alert_id,),
    )
    assert cursor.fetchone() == (False, False, True)


def test_same_alert_twice_for_the_same_user_fails(cursor):
    row = alert(client_alert_id="a_same")
    insert(cursor, row)
    assert "critical_alerts_user_id_client_alert_id_key" in refused(cursor, row)
    assert refused(cursor, {**row, "user_id": "test_alert_other_user"}) == ""  # another user may reuse the id


def test_update_delete_and_truncate_are_blocked(cursor):
    alert_id = insert(cursor, alert())
    for sql in [
        "UPDATE ai.critical_alerts SET action = 'sos' WHERE id = %s",
        "DELETE FROM ai.critical_alerts WHERE id = %s",
    ]:
        assert "append-only" in refused(cursor, sql, (alert_id,))
    assert "append-only" in refused(cursor, "TRUNCATE ai.critical_alerts")


@pytest.mark.parametrize("values, constraint", [
    ({"dismissed_at": "2026-11-18T09:59:59Z"}, "critical_alerts_dismissed_at_check"),
    ({"time_to_dismiss_seconds": -1}, "critical_alerts_time_to_dismiss_seconds_check"),
    ({"action": "ignored"}, "critical_alerts_action_check"),
    ({"trigger_source": "server"}, "critical_alerts_trigger_source_check"),
    ({"device_platform": "windows"}, "critical_alerts_device_platform_check"),
    ({"assessment_id": str(uuid.uuid4())}, "critical_alerts_assessment_id_fkey"),
])
def test_invalid_values_are_rejected(cursor, values, constraint):
    assert constraint in refused(cursor, alert(**values))


@pytest.mark.parametrize("column", ["user_id", "client_alert_id", "shown_at", "action", "trigger_vital",
                                    "trigger_value", "trigger_source", "country"])
def test_required_columns(cursor, column):
    row = alert()
    row.pop(column)
    assert "null value" in refused(cursor, row)


@pytest.mark.parametrize("action", ["sos", "call_911", "dismissed", "none"])
def test_every_action_is_accepted(cursor, action):
    assert refused(cursor, alert(action=action, trigger_source="ai_assessment", device_platform="ios")) == ""


def test_node_backend_can_insert_and_read_but_not_change(cursor):
    # 3b560c27ccd3 gave INSERT + SELECT (user_id, client_alert_id); 7bcdf5cb3509 widened it to SELECT on the table.
    cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = 'node_backend'")
    if cursor.fetchone() is None:
        pytest.skip("node_backend login does not exist here")
    cursor.execute("""
        SELECT privilege_type FROM information_schema.role_table_grants
        WHERE grantee = 'node_backend' AND table_schema = 'ai' AND table_name = 'critical_alerts'
    """)
    assert {row[0] for row in cursor.fetchall()} == {"INSERT", "SELECT"}
    cursor.execute("SELECT has_table_privilege('node_backend', 'ai.audit_log', 'INSERT')")
    assert cursor.fetchone()[0] is True


# ---------- the migration itself, on a scratch database ----------

def alembic(url: URL, *args):
    env = {
        **os.environ,
        "DATABASE_URL": plain(url),
        # Never touch the node_backend login: logins are shared by every database on the server.
        "NODE_BACKEND_PASSWORD": "",
        "DB_ADMIN_USER": "no_admin_in_tests",
        "DB_ADMIN_PASSWORD": "",
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout + result.stderr


def table_exists(url: URL) -> bool:
    import psycopg

    with psycopg.connect(plain(url)) as connection:
        return connection.execute("SELECT to_regclass('ai.critical_alerts') IS NOT NULL").fetchone()[0]


@pytest.fixture
def scratch_database():
    admin_url = os.environ.get("MIGRATION_TEST_ADMIN_URL")
    if not admin_url:
        pytest.skip("set MIGRATION_TEST_ADMIN_URL to run the migration on a scratch database")
    psycopg = pytest.importorskip("psycopg")
    app_url = database_url()
    name = f"vs_migration_test_{uuid.uuid4().hex[:8]}"
    admin = psycopg.connect(admin_url, autocommit=True)
    admin.execute(f'CREATE DATABASE {name} OWNER "{app_url.username}"')
    try:
        yield app_url.set(database=name)
    finally:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.close()


def test_migration_on_an_empty_database_and_back_down_one_step(scratch_database):
    # Empty database -> head: every migration, then this one's downgrade and upgrade again.
    # Never below 7635008277b8: its downgrade drops the node_backend login for the whole server.
    alembic(scratch_database, "upgrade", "head")
    assert table_exists(scratch_database)

    alembic(scratch_database, "downgrade", PREVIOUS)
    assert not table_exists(scratch_database)
    assert REVISION not in alembic(scratch_database, "current")

    alembic(scratch_database, "upgrade", "head")
    assert table_exists(scratch_database)
    assert "No new upgrade operations detected" in alembic(scratch_database, "check")
