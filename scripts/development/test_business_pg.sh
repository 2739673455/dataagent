#!/usr/bin/env bash
# Run any pytest selection against a fresh throwaway database, never the configured business DB.
set -euo pipefail
cd "$(dirname "$0")/../.."
export PATH="${PGBIN:-$(pg_config --bindir)}:$PATH"
python="${PYTHON:-.venv/bin/python}"
cluster=$(mktemp -d)
port=$("$python" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
cleanup() {
    pg_ctl -D "$cluster/data" -m fast -w stop >/dev/null 2>&1 || true
    rm -rf -- "$cluster"
}
trap cleanup EXIT
# The loopback test cluster uses SCRAM, even on multiuser development hosts.
pw=$("$python" -c 'import secrets; print(secrets.token_urlsafe(32))')
printf '%s\n' "$pw" > "$cluster/password"
chmod 600 "$cluster/password"
args=()
if [[ -n "${PGSHARE:-}" ]]; then args+=(-L "$PGSHARE"); fi
initdb -D "$cluster/data" -U pgadmin --auth=scram-sha-256 --pwfile="$cluster/password" --no-locale -E UTF8 "${args[@]}" >/dev/null
pg_ctl -D "$cluster/data" -l "$cluster/postgres.log" -o "-h 127.0.0.1 -k '' -p $port" -w start >/dev/null
export PGHOST=127.0.0.1 PGPORT="$port" PGUSER=pgadmin PGPASSWORD="$pw"
createdb ecommerce
export DATAAGENT_TEST_PG_DSN="postgresql://pgadmin:$pw@127.0.0.1:$port/ecommerce"
export DATAAGENT_DISPOSABLE_INIT_TEST=1
"$python" -m pytest "$@"
