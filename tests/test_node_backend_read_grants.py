# Tests for migration 7bcdf5cb3509: node_backend read grants for doctor and supervisor views.
#
# The migration test is opt-in, like tests/test_critical_alerts.py: set MIGRATION_TEST_ADMIN_URL to an admin login
# on the same server (it creates and drops a scratch database), e.g. for Docker:
#   MIGRATION_TEST_ADMIN_URL=postgresql://postgres:<password>@localhost:5433/postgres
# The node_backend login must already exist on that server (logins are shared by every database); the test never
# creates, changes or drops it. Privileges are checked with has_table_privilege / has_column_privilege, so the
# login's password is not needed.
# Run with: pytest

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from tests.test_critical_alerts import ROOT, alembic, plain, scratch_database  # noqa: F401  (fixture)

REVISION = "7bcdf5cb3509"
PREVIOUS = "3b560c27ccd3"
NODE_ROLE = "node_backend"
READ_TABLES = ["motor_results", "vitals_readings", "critical_alerts"]


def test_revision_follows_the_critical_alerts_migration_and_is_the_head():
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert scripts.get_revision(REVISION).down_revision == PREVIOUS
    assert scripts.get_heads() == [REVISION]


class Privileges:
    def __init__(self, url):
        import psycopg

        self.connection = psycopg.connect(plain(url))

    def table(self, table: str, privilege: str) -> bool:
        return self.connection.execute(
            "SELECT has_table_privilege(%s, %s, %s)", (NODE_ROLE, f"ai.{table}", privilege)
        ).fetchone()[0]

    def column(self, table: str, column: str, privilege: str = "SELECT") -> bool:
        return self.connection.execute(
            "SELECT has_column_privilege(%s, %s, %s, %s)", (NODE_ROLE, f"ai.{table}", column, privilege)
        ).fetchone()[0]

    def table_grants(self, table: str) -> set:
        rows = self.connection.execute("""
            SELECT privilege_type FROM information_schema.role_table_grants
            WHERE grantee = %s AND table_schema = 'ai' AND table_name = %s
        """, (NODE_ROLE, table)).fetchall()
        return {row[0] for row in rows}

    def close(self):
        self.connection.close()


@pytest.fixture
def migrated(scratch_database):  # noqa: F811
    import psycopg

    with psycopg.connect(plain(scratch_database)) as connection:
        if connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (NODE_ROLE,)).fetchone() is None:
            pytest.skip(f"the {NODE_ROLE} login does not exist on this server")
    alembic(scratch_database, "upgrade", "head")
    return scratch_database


def test_upgrade_gives_select_only(migrated):
    privileges = Privileges(migrated)
    try:
        for table in READ_TABLES:
            assert privileges.table(table, "SELECT"), table
            for change in ["UPDATE", "DELETE", "TRUNCATE"]:
                assert not privileges.table(table, change), (table, change)
        assert privileges.table_grants("motor_results") == {"SELECT"}
        assert privileges.table_grants("vitals_readings") == {"SELECT", "INSERT"}
        assert privileges.table_grants("critical_alerts") == {"SELECT", "INSERT"}
        # Not granted: internal cost data, and the audit log stays write-only for Node.
        assert privileges.table_grants("ai_usage") == set()
        assert not privileges.table("ai_usage", "SELECT")
        assert not privileges.table("audit_log", "SELECT")
        assert privileges.table("audit_log", "INSERT")
    finally:
        privileges.close()


def test_downgrade_removes_the_table_selects_and_keeps_the_duplicate_check_columns(migrated):
    alembic(migrated, "downgrade", PREVIOUS)
    privileges = Privileges(migrated)
    try:
        for table in READ_TABLES:
            assert not privileges.table(table, "SELECT"), table
        assert privileges.table_grants("motor_results") == set()
        assert privileges.table_grants("vitals_readings") == {"INSERT"}
        assert privileges.table_grants("critical_alerts") == {"INSERT"}
        # The column-level SELECTs of c4e8a1f2b903 and 3b560c27ccd3 are back; other columns stay unreadable.
        assert privileges.column("vitals_readings", "user_id") and privileges.column("vitals_readings", "client_reading_id")
        assert privileges.column("critical_alerts", "user_id") and privileges.column("critical_alerts", "client_alert_id")
        assert not privileges.column("vitals_readings", "heart_rate")
        assert not privileges.column("critical_alerts", "trigger_value")
    finally:
        privileges.close()

    alembic(migrated, "upgrade", "head")
    privileges = Privileges(migrated)
    try:
        assert all(privileges.table(table, "SELECT") for table in READ_TABLES)
    finally:
        privileges.close()
