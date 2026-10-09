"""Development scheduler: a Celery-beat stand-in for machines without Redis.

Run with ``WATCHGRID_LOCAL_NO_REDIS=true`` so tasks execute eagerly in-process.
Production uses ``celery beat`` (see docker-compose.yml) — never this command.
"""

import time

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.analytics.tasks import rollup_monitor_stats
from apps.checks.tasks import dispatch_due_monitors
from apps.notifications.tasks import send_incident_reminders


class Command(BaseCommand):
    help = "Dispatch due monitors in a loop (development only; uses eager Celery)."

    def add_arguments(self, parser):
        parser.add_argument("--tick", type=int, default=settings.SCHEDULER_TICK_SECONDS)

    def handle(self, *args, tick: int, **options):
        if not getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
            self.stderr.write(
                "Celery is not in eager mode; start `celery beat` and workers instead, or set "
                "WATCHGRID_LOCAL_NO_REDIS=true for a single-process development loop."
            )
            return
        self.stdout.write(self.style.SUCCESS(f"Scheduler running every {tick}s — Ctrl+C to stop."))
        iteration = 0
        try:
            while True:
                dispatched = dispatch_due_monitors()
                if dispatched:
                    self.stdout.write(f"dispatched {dispatched} check(s)")
                if iteration % max(1, 60 // tick) == 0:
                    send_incident_reminders()
                if iteration % max(1, 300 // tick) == 0:
                    rollup_monitor_stats()
                iteration += 1
                time.sleep(tick)
        except KeyboardInterrupt:
            self.stdout.write("Scheduler stopped.")
