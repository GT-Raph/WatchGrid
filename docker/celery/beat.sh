#!/bin/sh
# Celery beat: the single active scheduler (Section 25.1). Run exactly one instance;
# double dispatch is still prevented in the database (SKIP LOCKED + unique execution keys).
set -eu

exec celery -A config beat \
    --schedule /tmp/celerybeat-schedule \
    --loglevel "${LOG_LEVEL:-info}"
