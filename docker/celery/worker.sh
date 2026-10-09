#!/bin/sh
# Celery worker for one workload (Section 25.1: separate queues per workload).
#
#   CELERY_QUEUES       comma-separated queues, e.g. "monitoring" or "notifications,default"
#   CELERY_WORKER_NAME  node name prefix (the host name is appended)
#   CELERY_CONCURRENCY  worker processes
set -eu

exec celery -A config worker \
    --queues "${CELERY_QUEUES:-default}" \
    --hostname "${CELERY_WORKER_NAME:-worker}@%h" \
    --concurrency "${CELERY_CONCURRENCY:-4}" \
    --loglevel "${LOG_LEVEL:-info}"
