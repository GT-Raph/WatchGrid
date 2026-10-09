"""Populate a local database with a realistic demo workspace.

The workspace itself (user, organization, projects, monitors, alert policies) is the
fixture ``fixtures/development/demo_workspace.json``. This command loads it and then
generates 48 hours of check history, incidents and application errors relative to
"now", which a static fixture cannot express.
"""

import random
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.analytics.services import rollup_hours
from apps.checks.models import MonitorCheck
from apps.errors.models import ErrorEvent, ErrorGroup, ErrorGroupUser
from apps.incidents.models import Incident, IncidentEvent
from apps.incidents.services import MAX_EVIDENCE_SAMPLES, check_sample
from apps.monitors.models import Monitor
from apps.organizations.models import Organization
from apps.projects.models import Project

FIXTURE = settings.BASE_DIR / "fixtures" / "development" / "demo_workspace.json"
DEMO_EMAIL = "demo@watchgrid.local"
DEMO_PASSWORD = "watchgrid-demo-2026"  # noqa: S105 - published demo credential, DEBUG-only

# Monitor name -> (typical latency in ms, history profile)
PROFILES = {
    "Main Production Site": (280, "steady"),
    "Checkout API": (190, "outage"),
    "Customer Portal": (420, "slow"),
    "Payments Webhook": (150, "steady"),
    "Search Service": (230, "flaky"),
}


class Command(BaseCommand):
    help = "Load the demo workspace fixture and generate 48h of checks, incidents and errors."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete the existing demo organization first.")

    @transaction.atomic
    def handle(self, *args, reset: bool, **options):
        if not settings.DEBUG:
            raise CommandError("seed_demo creates an account with a published password and only runs with DEBUG=True.")
        random.seed(7)
        if reset:
            Organization.objects.filter(slug="demo").delete()
        if Organization.objects.filter(slug="demo").exists():
            self.stdout.write("Demo organization already exists (use --reset to rebuild).")
            return

        call_command("loaddata", FIXTURE, verbosity=0)
        now = timezone.now().replace(second=0, microsecond=0)
        start = now - timedelta(hours=48)
        monitors = Monitor.objects.filter(project__organization__slug="demo").order_by("name")
        for index, monitor in enumerate(monitors):
            base, profile = PROFILES[monitor.name]
            monitor.next_check_at = now + timedelta(seconds=30 + index * 7)
            self._history(monitor, start, now, base, profile)
        rollup_hours(start, now + timedelta(hours=1))
        self._errors(Project.objects.get(organization__slug="demo", slug="storefront"), now)
        self.stdout.write(self.style.SUCCESS(f"Demo ready. Sign in as {DEMO_EMAIL} / {DEMO_PASSWORD}"))

    def _history(self, monitor, start, now, base, profile):
        step = timedelta(seconds=monitor.interval_seconds)
        outage_start = now - timedelta(hours=7, minutes=12)
        outage_end = outage_start + timedelta(minutes=23)
        active_outage = profile == "outage"
        checks = []
        moment = start
        i = 0
        while moment < now:
            jitter = random.gauss(0, base * 0.12)
            latency = max(40, int(base + jitter + (base * 1.8 if 13 <= moment.hour <= 15 else 0) * random.random()))
            outcome, status, category, message = "success", 200, "", ""
            if profile == "outage" and outage_start <= moment < outage_end:
                outcome, status, category, message = "failure", 503, "http", "Unexpected HTTP status 503."
            elif active_outage and moment >= now - timedelta(minutes=9):
                outcome, status, category, message = "failure", None, "timeout", "Timed out waiting for the response."
                latency = 10000
            elif profile == "flaky" and random.random() < 0.015:
                outcome, status, category, message = "failure", None, "connection", "Connection reset by peer."
            elif profile == "slow" and moment >= now - timedelta(minutes=40):
                latency = int(base * 2.6 + random.random() * 200)
            if (
                monitor.response_time_threshold_ms
                and outcome == "success"
                and latency > monitor.response_time_threshold_ms
            ):
                outcome, category, message = "degraded", "performance", f"Response took {latency} ms."
            checks.append(
                MonitorCheck(
                    monitor=monitor,
                    execution_key=f"seed:{i}",
                    scheduled_for=moment,
                    checked_at=moment,
                    outcome=outcome,
                    success=outcome in ("success", "degraded"),
                    status_code=status,
                    response_time_ms=latency if outcome != "failure" or category == "timeout" else None,
                    error_category=category,
                    error_message=message,
                    resolved_ip="93.184.216.34",
                )
            )
            moment += step
            i += 1
        MonitorCheck.objects.bulk_create(checks, batch_size=2000)
        last = checks[-1]

        if profile == "outage":
            self._incident(monitor, outage_start, outage_end, "major", "http", "Unexpected HTTP status 503.")
            failing_since = now - timedelta(minutes=9)
            monitor.status = Monitor.Status.DOWN
            monitor.consecutive_failures = 9
            monitor.failing_since = failing_since
            self._incident(monitor, failing_since, None, "critical", "timeout", "Timed out waiting for the response.")
        elif profile == "slow":
            monitor.status = Monitor.Status.DEGRADED
            monitor.consecutive_soft_failures = 40
            monitor.degraded_since = now - timedelta(minutes=40)
            self._incident(monitor, monitor.degraded_since, None, "minor", "performance", "Response time above 900 ms.")
        else:
            monitor.status = Monitor.Status.OPERATIONAL
            monitor.consecutive_successes = 10
        monitor.last_checked_at = last.checked_at
        monitor.last_response_time_ms = last.response_time_ms
        monitor.last_status_code = last.status_code
        monitor.last_error_category = last.error_category
        monitor.last_status_change_at = monitor.failing_since or monitor.degraded_since or start
        monitor.tls_expires_at = now + timedelta(days=random.choice([12, 64, 210]))
        monitor.save()

    def _incident(self, monitor, detected, resolved, severity, failure, summary):
        key = "performance" if failure == "performance" else "availability"
        # Evidence in the same shape the check pipeline records: the failing checks that
        # opened and sustained the incident, then the successes that resolved it.
        checks = monitor.checks.filter(checked_at__gte=detected).order_by("checked_at")
        failing = list(checks.filter(checked_at__lt=resolved or timezone.now()).exclude(outcome="success"))
        recovering = list(checks.filter(checked_at__gte=resolved, outcome="success")[:2]) if resolved else []
        evidence = {"samples": [check_sample(check) for check in failing[-MAX_EVIDENCE_SAMPLES:]]}
        if recovering:
            evidence["recovery_samples"] = [check_sample(check) for check in recovering]
            resolved = recovering[-1].checked_at  # resolves on the second consecutive success
        incident = Incident.objects.create(
            project=monitor.project,
            monitor=monitor,
            incident_key=key,
            title=f"{monitor.name} is {'degraded' if key == 'performance' else 'down'}",
            severity=severity,
            failure_type=failure,
            summary=summary,
            detected_at=detected,
            confirmed_at=detected + timedelta(minutes=2),
            last_seen_at=resolved or timezone.now(),
            failure_count=len(failing),
            evidence=evidence,
            status=Incident.Status.RESOLVED if resolved else Incident.Status.OPEN,
            resolved_at=resolved,
            resolution_type="automatic" if resolved else "",
        )
        opened = IncidentEvent.objects.create(incident=incident, kind="opened", to_status="open", message=summary)
        # created_at is auto_now_add; back-date the timeline to when the events happened.
        IncidentEvent.objects.filter(pk=opened.pk).update(created_at=incident.confirmed_at)
        if resolved:
            closed = IncidentEvent.objects.create(
                incident=incident,
                kind="resolved",
                from_status="open",
                to_status="resolved",
                message="Recovered after 2 consecutive successful check(s).",
            )
            IncidentEvent.objects.filter(pk=closed.pk).update(created_at=resolved)
        return incident

    def _errors(self, project, now):
        samples = [
            ("DatabaseError", "Unable to acquire database connection", "/api/payments/", "POST", 140, 37),
            ("TimeoutError", "Upstream inventory service timed out after <n>s", "/api/cart/", "GET", 58, 21),
            ("KeyError", "'shipping_address'", "/api/checkout/", "POST", 12, 9),
            ("TypeError", "Cannot read properties of undefined (reading 'price')", "/cart", "GET", 230, 118),
        ]
        for error_type, message, endpoint, method, count, users in samples:
            first_seen = now - timedelta(hours=random.randint(6, 40))
            group = ErrorGroup.objects.create(
                project=project,
                fingerprint=uuid.uuid4().hex + uuid.uuid4().hex,
                environment="production",
                level="error",
                error_type=error_type,
                normalized_message=message,
                sample_message=message,
                endpoint=endpoint,
                http_method=method,
                top_frame="views.py:handler",
                first_seen=first_seen,
                last_seen=now - timedelta(minutes=random.randint(1, 50)),
                occurrence_count=count,
                stored_event_count=min(count, 20),
                affected_users=users,
                last_release="2.6.0",
            )
            ErrorGroupUser.objects.bulk_create(
                [
                    ErrorGroupUser(
                        error_group=group, user_hash=uuid.uuid4().hex * 2, first_seen=first_seen, last_seen=now
                    )
                    for _ in range(users)
                ]
            )
            ErrorEvent.objects.bulk_create(
                [
                    ErrorEvent(
                        error_group=group,
                        project=project,
                        occurred_at=first_seen + (now - first_seen) * (n / 20),
                        received_at=now,
                        level="error",
                        message=message,
                        endpoint=endpoint,
                        http_method=method,
                        application_version="2.6.0",
                        stack_trace=(
                            "Traceback (most recent call last):\n"
                            f'  File "/app/shop/views.py", line 88, in handler\n    return service.run(request)\n'
                            f"{error_type}: {message}"
                        ),
                        metadata={"request_id": uuid.uuid4().hex[:12]},
                    )
                    for n in range(min(count, 20))
                ]
            )
