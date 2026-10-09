"""End-to-end journey through the HTML dashboard (Section 27.1 "End-to-end").

A new user signs up, creates an organization, project and monitor in the browser; the
scheduler runs; the target fails three times and recovers. The dashboard, incident
pages and email outbox are checked at each step. Only the network probe is scripted.
"""

from datetime import timedelta
from unittest import mock

import pytest
from django.core import mail
from django.test import Client
from django.utils import timezone

from apps.checks import tasks
from apps.checks.probe import ProbeResult
from apps.incidents.models import Incident
from apps.monitors.models import Monitor
from apps.monitors.state_machine import OUTCOME_FAILURE, OUTCOME_SUCCESS

pytestmark = pytest.mark.django_db


def failing():
    return ProbeResult(
        outcome=OUTCOME_FAILURE,
        checked_at=timezone.now(),
        status_code=503,
        response_time_ms=90,
        error_category="http",
        error_message="Unexpected HTTP status 503 (expected 200-299).",
    )


def healthy():
    return ProbeResult(outcome=OUTCOME_SUCCESS, checked_at=timezone.now(), status_code=200, response_time_ms=140)


def tick(result, run_on_commit):
    Monitor.objects.update(next_check_at=timezone.now() - timedelta(seconds=1))
    with mock.patch.object(tasks, "execute_probe", return_value=result), run_on_commit():
        tasks.dispatch_due_monitors()


def test_sign_up_to_outage_to_recovery(run_on_commit):
    browser = Client()
    with run_on_commit():
        response = browser.post(
            "/register/",
            {"email": "founder@example.com", "password": "Long-enough-passphrase-1", "full_name": "Ama Founder"},
        )
    assert response.status_code == 302
    assert browser.get("/").url == "/welcome/"

    assert browser.post("/welcome/", {"name": "Kumasi Labs"}).url == "/projects/"
    hidden_organization = browser.get("/projects/new/").context["form"].initial["organization"]
    response = browser.post(
        "/projects/new/",
        {"name": "Storefront", "environment": "production", "description": "", "organization": hidden_organization},
    )
    assert response.status_code == 302, response.context and response.context["form"].errors

    project_id = browser.get("/monitors/new/").context["projects"][0].pk
    response = browser.post(
        "/monitors/new/",
        {
            "project": str(project_id),
            "name": "Checkout API",
            "monitor_type": "api",
            "url": "https://api.example.com/health",
            "http_method": "GET",
            "interval_seconds": "60",
            "timeout_seconds": "10",
            "expected_status": "200-299",
            "failure_threshold": "3",
            "recovery_threshold": "2",
            "max_redirects": "5",
            "follow_redirects": "on",
            "headers": "{}",
            "body": "",
            "assertion_mode": "none",
            "assertion_text": "",
        },
    )
    assert response.status_code == 302, response.context and response.context["form"].errors
    monitor = Monitor.objects.get(name="Checkout API")

    # Three failures in a row confirm one outage (MON-002).
    for _ in range(3):
        tick(failing(), run_on_commit)
    monitor.refresh_from_db()
    assert monitor.status == "down"
    overview = browser.get("/")
    assert b"1 monitor down" in overview.content
    assert overview.context["summary"]["incidents"]["active"] == 1
    incident = Incident.objects.get()
    page = browser.get(f"/incidents/{incident.pk}/")
    assert b"Unexpected HTTP status 503" in page.content
    assert any("DOWN" in message.subject for message in mail.outbox)

    # An operator acknowledges it from the incident page.
    browser.post(f"/incidents/{incident.pk}/", {"action": "status", "status": "acknowledged", "note": "On it"})
    incident.refresh_from_db()
    assert incident.status == "acknowledged"

    # Two healthy checks recover the monitor and close the incident (MON-004).
    tick(healthy(), run_on_commit)
    tick(healthy(), run_on_commit)
    monitor.refresh_from_db()
    incident.refresh_from_db()
    assert monitor.status == "operational"
    assert incident.status == "resolved" and incident.resolution_type == "automatic"
    assert any("RECOVERED" in message.subject for message in mail.outbox)
    detail = browser.get(f"/monitors/{monitor.pk}/")
    assert detail.status_code == 200
    assert detail.context["chart"]["failures"]  # the outage is visible on the chart
