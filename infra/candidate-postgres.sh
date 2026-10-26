#!/usr/bin/env bash
set -euo pipefail
app_password="$(cat /run/secrets/app_password)"
if [[ ! "$app_password" =~ ^[0-9a-f]{64}$ ]]; then
    echo 'Invalid candidate application password file' >&2
    exit 1
fi
# Only controlled hex enters the SQL literal. No password is passed on argv or logged.
psql --set ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE careerpilot_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '${app_password}';
ALTER DATABASE careerpilot_test OWNER TO careerpilot_app;
GRANT ALL ON SCHEMA public TO careerpilot_app;
SQL
unset app_password
