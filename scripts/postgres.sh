#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pgbin="$project_dir/.local/postgres/usr/lib/postgresql/17/bin"
pgdata="$project_dir/.local/pgdata"
pgsocket="$project_dir/.local/pgsocket"
export LD_LIBRARY_PATH="$project_dir/.local/postgres/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
mkdir -p "$pgsocket"
chmod 700 "$pgsocket"
case "${1:-start}" in
    start)
        if [[ ! -f "$pgdata/PG_VERSION" ]]; then
            "$pgbin/initdb" -D "$pgdata" --encoding=UTF8 --locale=C.UTF-8 --auth-local=trust --auth-host=reject
        fi
        if ! "$pgbin/pg_ctl" -D "$pgdata" status >/dev/null 2>&1; then
            "$pgbin/pg_ctl" -D "$pgdata" -l "$project_dir/.local/postgres.log" \
                -o "-k $pgsocket -p 55432 -c listen_addresses='' -c jit=off" -w start
        fi
        if [[ "$("$pgbin/psql" -h "$pgsocket" -p 55432 -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='wash_development'")" != "1" ]]; then
            "$pgbin/createdb" -h "$pgsocket" -p 55432 wash_development
        fi
        "$pgbin/pg_isready" -h "$pgsocket" -p 55432 -d wash_development
        ;;
    stop) "$pgbin/pg_ctl" -D "$pgdata" -w stop ;;
    *) echo 'Use start or stop.' >&2; exit 2 ;;
esac

