#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ "${DJANGO_DEBUG:-}" == "0" ]] || { echo 'Set DJANGO_DEBUG=0 for production.' >&2; exit 1; }
.venv/bin/python manage.py check --deploy --fail-level WARNING
exec .venv/bin/gunicorn config.wsgi:application --config config/gunicorn.conf.py
