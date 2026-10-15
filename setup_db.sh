#!/usr/bin/env bash
# One-step local database setup for the AI module (Mac).
# 1. Adds the DB_* settings to .env if missing (passwords are generated, not hard-coded)
# 2. Creates the logins and the database (db/bootstrap.sql, as the Postgres admin)
# 3. Creates the "ai" schema and tables with Alembic (alembic upgrade head)
# Safe to run again: existing .env values are kept, existing logins/database are skipped,
# and Alembic only runs migrations the database does not have yet.
#
# Run from the project folder:   bash setup_db.sh
# Delete the ai schema and start fresh (LOCAL ONLY, deletes all ai data):
#                                RESET_AI_SCHEMA=yes bash setup_db.sh

set -euo pipefail
cd "$(dirname "$0")"

command -v psql >/dev/null || { echo "ERROR: psql not found. Run: brew install postgresql@16"; exit 1; }
ALEMBIC=.venv/bin/alembic
[[ -x "$ALEMBIC" ]] || { echo "ERROR: $ALEMBIC not found. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }

# ---------------------------------------------------------------------------
# 1. Database settings in .env (added only if missing; your values are kept)
# ---------------------------------------------------------------------------
touch .env .env.example

add_setting() {
  # $1 = key, $2 = default value for .env
  grep -q "^$1=" .env         || echo "$1=$2" >> .env
  grep -q "^$1=" .env.example || echo "$1=" >> .env.example
}
new_password() { openssl rand -hex 16; }

add_setting DB_HOST          localhost
add_setting DB_PORT          5432
add_setting DB_NAME          vitalsigns
add_setting DB_APP_USER      vs_ai
add_setting DB_APP_PASSWORD  "$(new_password)"
add_setting DB_NODE_USER     vs_node
add_setting DB_NODE_PASSWORD "$(new_password)"

grep -qx ".env" .gitignore 2>/dev/null || echo ".env" >> .gitignore

set -a; source .env; set +a

for key in DB_HOST DB_PORT DB_NAME DB_APP_USER DB_APP_PASSWORD DB_NODE_USER DB_NODE_PASSWORD; do
  [[ -n "${!key:-}" ]] || { echo "ERROR: $key is empty in .env. Fill it in and run again."; exit 1; }
done

# ---------------------------------------------------------------------------
# 2. Create logins and database, then schema and tables (Alembic)
# ---------------------------------------------------------------------------
PSQL=(psql -X -q -v ON_ERROR_STOP=1 -h "$DB_HOST" -p "$DB_PORT")

echo "Creating logins and database ..."
"${PSQL[@]}" -d postgres \
  -v app_db="$DB_NAME" \
  -v app_user="$DB_APP_USER" -v app_password="$DB_APP_PASSWORD" \
  -v node_user="$DB_NODE_USER" -v node_password="$DB_NODE_PASSWORD" \
  -v has_node=true \
  -f db/bootstrap.sql

if [[ "${RESET_AI_SCHEMA:-}" == "yes" ]]; then
  if [[ "$DB_HOST" != "localhost" && "$DB_HOST" != "127.0.0.1" ]]; then
    echo "ERROR: RESET_AI_SCHEMA only works on localhost. Refusing to delete data on $DB_HOST."
    exit 1
  fi
  echo "Deleting the ai schema and all its tables (local reset) ..."
  PGPASSWORD="$DB_APP_PASSWORD" "${PSQL[@]}" -U "$DB_APP_USER" -d "$DB_NAME" \
    -c 'DROP SCHEMA IF EXISTS ai CASCADE'
fi

echo "Creating schema and tables (alembic upgrade head) ..."
# alembic/env.py reads the DB_* values from .env (or DATABASE_URL, if set).
"$ALEMBIC" upgrade head

echo
PGPASSWORD="$DB_APP_PASSWORD" "${PSQL[@]}" -U "$DB_APP_USER" -d "$DB_NAME" -c '\dt ai.*'

echo "Done. To view in TablePlus use the DB_* values in .env:"
echo "  Host $DB_HOST  Port $DB_PORT  User $DB_APP_USER  Database $DB_NAME  (password: DB_APP_PASSWORD in .env)"
