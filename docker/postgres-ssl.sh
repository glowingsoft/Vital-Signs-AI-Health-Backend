#!/usr/bin/env bash
# Creates the self-signed certificate Postgres serves to the NestJS backend (SSL over the private network).
#
# On the AI server, from the app folder:
#     sudo bash docker/postgres-ssl.sh <server-private-ip>
# Files go to /opt/postgres-ssl (outside the app folder, so rsync --delete and git never touch them):
#     server.crt  the certificate: copy it to the backend server (DB_SSL_CA). Not secret.
#     server.key  the private key: never leaves this server. Owned by the postgres user (uid 999), mode 600.
#
# The private IP is written into the certificate (subjectAltName), so clients can check the host name as well
# (sslmode=verify-full, or Node's default rejectUnauthorized check).
# Replacing an existing certificate (rotation): FORCE=yes sudo bash docker/postgres-ssl.sh <ip>
# Optional: a second argument sets another folder; PG_SSL_DAYS sets the validity (default 825 days).

set -euo pipefail

ip=${1:?"usage: sudo bash docker/postgres-ssl.sh <server-private-ip> [folder]"}
dir=${2:-/opt/postgres-ssl}
days=${PG_SSL_DAYS:-825}
pg_uid=999  # the postgres user inside the postgres:16 image

if [[ $(id -u) -ne 0 ]]; then
  echo "ERROR: run with sudo (the key must be owned by uid $pg_uid)." >&2
  exit 1
fi
if [[ -e "$dir/server.key" && "${FORCE:-}" != "yes" ]]; then
  echo "ERROR: $dir/server.key already exists. To replace it: FORCE=yes sudo bash $0 $ip" >&2
  exit 1
fi

mkdir -p "$dir"
chmod 755 "$dir"

# Written next to the old files first, then moved, so Postgres never sees half-written files.
openssl req -x509 -newkey rsa:3072 -nodes -sha256 -days "$days" \
  -subj "/CN=vitalsigns-ai-postgres" \
  -addext "subjectAltName=IP:$ip,DNS:postgres,DNS:localhost" \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,digitalSignature,keyEncipherment,keyCertSign" \
  -addext "extendedKeyUsage=serverAuth" \
  -keyout "$dir/server.key.new" -out "$dir/server.crt.new" 2>/dev/null

# Postgres refuses a key that others can read.
chown "$pg_uid:$pg_uid" "$dir/server.key.new" "$dir/server.crt.new"
chmod 600 "$dir/server.key.new"
chmod 644 "$dir/server.crt.new"
mv -f "$dir/server.key.new" "$dir/server.key"
mv -f "$dir/server.crt.new" "$dir/server.crt"

echo "Created $dir/server.crt and $dir/server.key (valid $days days, for IP $ip):"
openssl x509 -in "$dir/server.crt" -noout -subject -enddate -ext subjectAltName
