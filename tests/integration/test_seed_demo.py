"""The demo workspace (``manage.py seed_demo``) is internally consistent."""

import pytest
from django.core.management import CommandError, call_command
from django.utils.dateparse import parse_datetime

from apps.incidents.models import Incident
from apps.monitors.models import Monitor

pytestmark = pytest.mark.django_db


def test_refuses_without_debug(settings):
    settings.DEBUG = False
    with pytest.raises(CommandError):
        call_command("seed_demo")


def test_incidents_carry_evidence_matching_their_checks(settings):
    settings.DEBUG = True
    call_command("seed_demo")

    assert Monitor.objects.filter(project__organization__slug="demo").count() == 5
    incidents = list(Incident.objects.order_by("detected_at"))
    assert len(incidents) == 3
    for incident in incidents:
        samples = incident.evidence["samples"]
        assert samples, incident.title
        assert incident.failure_count >= len(samples)
        assert all(sample["outcome"] != "success" for sample in samples)
        opened = incident.events.get(kind="opened")
        assert opened.created_at == incident.confirmed_at

    resolved = next(incident for incident in incidents if incident.status == Incident.Status.RESOLVED)
    recovery = resolved.evidence["recovery_samples"]
    assert [sample["outcome"] for sample in recovery] == ["success", "success"]
    assert resolved.resolved_at == parse_datetime(recovery[-1]["checked_at"])
    assert resolved.events.get(kind="resolved").created_at == resolved.resolved_at


def test_reset_rebuilds_instead_of_duplicating(settings):
    settings.DEBUG = True
    call_command("seed_demo")
    call_command("seed_demo", "--reset")

    assert Monitor.objects.filter(project__organization__slug="demo").count() == 5
    assert Incident.objects.count() == 3
