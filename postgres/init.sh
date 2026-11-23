#!/bin/bash
# Runs once, the first time the Postgres container starts with an empty data volume.
# Creates the logins and the database with the same postgres/bootstrap.sql as setup_db.sh.
# The "ai" schema and tables are created afterwards by the newest version's "migrate" container (alembic upgrade head).

set -euo pipefail

# Inside the container, connections over the local socket need no password during setup.
PSQL=(psql -X -q -v ON_ERROR_STOP=1)

echo "Creating logins and database ..."
"${PSQL[@]}" -U "$POSTGRES_USER" -d postgres \
  -v app_db="$DB_NAME" \
  -v app_user="$DB_APP_USER" -v app_password="$DB_APP_PASSWORD" \
  -v node_user="$DB_NODE_USER" -v node_password="$DB_NODE_PASSWORD" \
  -v has_node=true \
  -f /sql/bootstrap.sql
