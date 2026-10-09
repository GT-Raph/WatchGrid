#!/bin/sh
# Container entrypoint for every WatchGrid process (web, workers, beat).
#
#   watchgrid-entrypoint migrate     apply migrations and collect static files, then exit
#   watchgrid-entrypoint <command>   run the command (default: gunicorn, see Dockerfile)
set -eu

if [ "${1:-}" = "migrate" ]; then
    python manage.py migrate --noinput
    python manage.py collectstatic --noinput
    exit 0
fi

exec "$@"
