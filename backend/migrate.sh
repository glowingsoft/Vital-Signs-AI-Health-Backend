#!/bin/sh
# Run by the "migrate" container of every version (docker-compose.yml), before the API and worker start.
#   1. Waits until the platform's Postgres accepts connections (it lives in another Compose project,
#      so "depends_on" cannot wait for it).
#   2. RUN_MIGRATIONS=true  -> "alembic upgrade head" on the shared database (the NEWEST version only).
#      RUN_MIGRATIONS=false -> nothing: an older version uses the schema as the newest version left it.

set -eu

if [ "${RUN_MIGRATIONS:-true}" != "true" ]; then
  echo "RUN_MIGRATIONS=${RUN_MIGRATIONS}: version ${API_VERSION:-?} does not change the database. Done."
  exit 0
fi

echo "Waiting for Postgres at ${DB_HOST}:${DB_PORT} ..."
python - <<'EOF'
import os, socket, sys, time

# Postgres only listens on TCP once its first-time setup is finished, so an open port = ready.
for _ in range(60):
    try:
        socket.create_connection((os.environ["DB_HOST"], int(os.environ["DB_PORT"])), timeout=2).close()
        sys.exit(0)
    except OSError:
        time.sleep(2)
sys.exit("Postgres is not reachable after 2 minutes. Is the platform running? (docker-compose.platform.yml)")
EOF

echo "Running migrations for version ${API_VERSION:-?} ..."
exec alembic upgrade head
