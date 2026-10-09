"""Celery application for the execution plane (Section 12.1)."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

app = Celery("watchgrid")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
