#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ -z "${DATABASE_URL:-}" ]]; then bash scripts/postgres.sh start; fi
.venv/bin/python manage.py check
exec .venv/bin/python manage.py runserver 127.0.0.1:8000 --noreload

