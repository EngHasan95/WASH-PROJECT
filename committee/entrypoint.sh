#!/usr/bin/env bash
set -euo pipefail
umask 077
mkdir -p /committee/media/policies
if [[ ! -f /committee/media/policies/violation-fees-source.jpg ]]; then
    cp /app/reference/violation-fees-source.jpg /committee/media/policies/violation-fees-source.jpg
    chmod 600 /committee/media/policies/violation-fees-source.jpg
fi
python manage.py migrate --noinput
python manage.py seed_committee
python manage.py check
python scripts/backup_scheduler.py /committee/backups &
exec gunicorn config.wsgi:application --bind 0.0.0.0:8080 --workers 2 --threads 2 --timeout 60 --error-logfile -
