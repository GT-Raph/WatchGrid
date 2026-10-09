"""Notification routing, deduplication and retry (Section 17, NOT-001, BR-006)."""

from unittest import mock

import pytest
from django.core import mail
from django.utils import timezone

from apps.incidents.models import Incident
from apps.notifications import services
from apps.notifications.models import AlertPolicy, MaintenanceWindow, Notification
from apps.notifications.providers import EmailProvider, TransientDeliveryError, WebhookProvider
from tests.factories import MonitorFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def monitor(project):
    return MonitorFactory(project=project, name="Checkout API")


@pytest.fixture
def incident(project, monitor):
    now = timezone.now()
    return Incident.objects.create(
        project=project,
        monitor=monitor,
        title="Checkout API is down",
        severity="critical",
        detected_at=now,
        confirmed_at=now,
        last_seen_at=now,
        failure_type="timeout",
    )


@pytest.fixture
def policy(project):
    return AlertPolicy.objects.create(
        project=project,
        name="On-call",
        event_types=["monitor_down", "monitor_recovered"],
        channels=services.store_channels([{"type": "email", "to": ["oncall@example.com"], "to_roles": ["owner"]}]),
    )


def test_email_sent_to_explicit_addresses_and_roles(incident, policy, run_on_commit):
    with run_on_commit():
        rows = services.create_incident_notifications(incident, "opened")
    assert sorted(r.recipient for r in rows) == ["oncall@example.com", "owner@example.com"]
    assert sorted(m.to[0] for m in mail.outbox) == ["oncall@example.com", "owner@example.com"]
    assert Notification.objects.filter(status="sent").count() == 2
    assert "Checkout API" in mail.outbox[0].subject and "DOWN" in mail.outbox[0].subject


def test_dedupe_same_event_never_creates_second_row(incident, policy, run_on_commit):
    with run_on_commit():
        services.create_incident_notifications(incident, "opened")
        services.create_incident_notifications(incident, "opened")
    assert Notification.objects.count() == 2
    assert len(mail.outbox) == 2


def test_not_001_temporary_failure_retries_without_duplicate_delivery(incident, policy):
    rows = services.create_incident_notifications(incident, "opened")
    target = rows[0]
    with mock.patch.object(EmailProvider, "send", side_effect=TransientDeliveryError("smtp down")):
        assert services.attempt_delivery(target.id) == "retry"
    target.refresh_from_db()
    assert target.status == "pending" and target.attempts == 1 and "smtp down" in target.last_error

    assert services.attempt_delivery(target.id) == "sent"
    assert services.attempt_delivery(target.id) == "skipped"  # redelivered task message
    sent_to = [m.to[0] for m in mail.outbox]
    assert sent_to.count(target.recipient) == 1


def test_delivery_gives_up_after_max_attempts(incident, policy, settings):
    settings.NOTIFICATION_MAX_RETRIES = 2
    row = services.create_incident_notifications(incident, "opened")[0]
    with mock.patch.object(EmailProvider, "send", side_effect=TransientDeliveryError("nope")):
        assert services.attempt_delivery(row.id) == "retry"
        assert services.attempt_delivery(row.id) == "failed"
    row.refresh_from_db()
    assert row.status == "failed"


def test_concurrent_sending_lease_prevents_double_send(incident, policy):
    row = services.create_incident_notifications(incident, "opened")[0]
    Notification.objects.filter(pk=row.pk).update(status="sending")
    assert services.attempt_delivery(row.id) == "skipped"


def test_br_006_maintenance_window_suppresses_alerts(incident, policy, project, run_on_commit):
    now = timezone.now()
    MaintenanceWindow.objects.create(
        project=project,
        title="DB upgrade",
        starts_at=now - timezone.timedelta(minutes=5),
        ends_at=now + timezone.timedelta(hours=1),
    )
    with run_on_commit():
        rows = services.create_incident_notifications(incident, "opened")
    assert rows == []
    assert set(Notification.objects.values_list("status", flat=True)) == {"suppressed"}
    assert not mail.outbox


def test_maintenance_for_other_monitor_does_not_suppress(incident, policy, project, run_on_commit):
    now = timezone.now()
    MaintenanceWindow.objects.create(
        project=project,
        monitor=MonitorFactory(project=project),
        title="Other",
        starts_at=now - timezone.timedelta(minutes=5),
        ends_at=now + timezone.timedelta(hours=1),
    )
    with run_on_commit():
        services.create_incident_notifications(incident, "opened")
    assert len(mail.outbox) == 2


def test_delay_suppresses_alert_if_incident_resolved_first(incident, policy):
    policy.delay_seconds = 300
    policy.save()
    row = services.create_incident_notifications(incident, "opened")[0]
    assert row.deliver_after > timezone.now() + timezone.timedelta(seconds=250)
    incident.status = "resolved"
    incident.resolved_at = timezone.now()
    incident.save()
    assert services.attempt_delivery(row.id) == "suppressed"


def test_policy_filters_by_event_severity_and_monitor(incident, project, monitor):
    AlertPolicy.objects.create(
        project=project,
        name="degraded only",
        event_types=["monitor_degraded"],
        channels=services.store_channels([{"type": "email", "to": ["a@example.com"]}]),
    )
    AlertPolicy.objects.create(
        project=project,
        name="critical+ but other monitor",
        event_types=["monitor_down"],
        monitor=MonitorFactory(project=project),
        channels=services.store_channels([{"type": "email", "to": ["b@example.com"]}]),
    )
    AlertPolicy.objects.create(
        project=project,
        name="disabled",
        enabled=False,
        event_types=["monitor_down"],
        channels=services.store_channels([{"type": "email", "to": ["c@example.com"]}]),
    )
    incident.severity = "minor"
    AlertPolicy.objects.create(
        project=project,
        name="major+",
        min_severity="major",
        event_types=["monitor_down"],
        channels=services.store_channels([{"type": "email", "to": ["d@example.com"]}]),
    )
    assert services.create_incident_notifications(incident, "opened") == []


def test_reminders_escalate_once_per_interval(incident, policy):
    policy.repeat_interval_minutes = 15
    policy.save()
    later = incident.confirmed_at + timezone.timedelta(minutes=31)
    assert services.create_due_reminders(now=later) == 2  # level 2, both recipients
    assert services.create_due_reminders(now=later) == 0
    levels = set(Notification.objects.values_list("escalation_level", flat=True))
    assert levels == {2}


def test_webhook_channel_encrypts_url_and_sends_via_safe_client(incident, project):
    policy = AlertPolicy.objects.create(
        project=project,
        name="Slack",
        event_types=["monitor_down"],
        channels=services.store_channels([{"type": "webhook", "url": "https://hooks.example.com/T000/B000/XXXX"}]),
    )
    assert "XXXX" not in str(policy.channels)
    assert services.public_channels(policy.channels) == [
        {"type": "webhook", "url_display": "https://hooks.example.com/…"}
    ]
    row = services.create_incident_notifications(incident, "opened")[0]
    with mock.patch("apps.notifications.providers.perform_request") as request:
        request.return_value = mock.Mock(status=200)
        assert services.attempt_delivery(row.id) == "sent"
    sent_url = request.call_args.args[0]
    assert sent_url == "https://hooks.example.com/T000/B000/XXXX"
    assert b'"event": "monitor_down"' in request.call_args.kwargs["body"]


def test_webhook_to_private_address_fails_permanently(incident, project):
    AlertPolicy.objects.create(
        project=project,
        name="Bad hook",
        event_types=["monitor_down"],
        channels=services.store_channels([{"type": "webhook", "url": "https://internal.example.com/hook"}]),
    )
    row = services.create_incident_notifications(incident, "opened")[0]
    assert services.attempt_delivery(row.id) == "failed"
    row.refresh_from_db()
    assert "network policy" in row.last_error


def test_webhook_server_errors_are_retryable():
    provider = WebhookProvider()
    with (
        mock.patch("apps.notifications.providers.perform_request", return_value=mock.Mock(status=503)),
        pytest.raises(TransientDeliveryError),
    ):
        provider.send(recipient="x", target={"url": "https://hooks.example.com/x"}, payload={})


def test_alert_policy_api_round_trip_hides_webhook_secret(owner_client, project):
    payload = {
        "project": str(project.id),
        "name": "Ops",
        "event_types": ["monitor_down", "monitor_recovered"],
        "min_severity": "major",
        "delay_seconds": 60,
        "channels": [
            {"type": "email", "to": ["Ops@Example.com"], "to_roles": ["admin"]},
            {"type": "webhook", "url": "https://hooks.example.com/secret-path"},
        ],
    }
    response = owner_client.post("/api/v1/alert-policies/", payload, format="json")
    assert response.status_code == 201, response.content
    body = response.json()
    assert "secret-path" not in response.content.decode()
    assert body["channels"][0]["to"] == ["ops@example.com"]
    # Renaming without resending the webhook URL keeps it.
    response = owner_client.patch(
        f"/api/v1/alert-policies/{body['id']}/",
        {"name": "Ops renamed", "channels": [{"type": "email", "to": ["ops@example.com"]}, {"type": "webhook"}]},
        format="json",
    )
    assert response.status_code == 200, response.content
    policy = AlertPolicy.objects.get(pk=body["id"])
    from common.security.crypto import decrypt_json

    assert decrypt_json(policy.channels[1]["url_encrypted"]) == "https://hooks.example.com/secret-path"


def test_alert_policy_validation(owner_client, project):
    response = owner_client.post(
        "/api/v1/alert-policies/",
        {
            "project": str(project.id),
            "name": "x",
            "event_types": ["monitor_down"],
            "channels": [{"type": "webhook", "url": "http://hooks.example.com/plain"}],
        },
        format="json",
    )
    assert response.status_code == 400
    response = owner_client.post(
        "/api/v1/alert-policies/",
        {
            "project": str(project.id),
            "name": "x",
            "event_types": ["monitor_down"],
            "repeat_interval_minutes": 1,
            "channels": [{"type": "email", "to": ["a@example.com"]}],
        },
        format="json",
    )
    assert response.status_code == 400


def test_maintenance_window_api(owner_client, project):
    response = owner_client.post(
        "/api/v1/maintenance-windows/",
        {
            "project": str(project.id),
            "title": "Upgrade",
            "starts_at": "2030-01-01T00:00:00Z",
            "ends_at": "2029-12-31T00:00:00Z",
        },
    )
    assert response.status_code == 400
    response = owner_client.post(
        "/api/v1/maintenance-windows/",
        {
            "project": str(project.id),
            "title": "Upgrade",
            "starts_at": "2030-01-01T00:00:00Z",
            "ends_at": "2030-01-01T02:00:00Z",
        },
    )
    assert response.status_code == 201
    assert response.json()["is_active"] is False
