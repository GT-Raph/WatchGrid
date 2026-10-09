"""Every link WatchGrid emails must open a real dashboard page."""

import re
from urllib.parse import urlsplit

import pytest
from django.core import mail
from django.urls import resolve
from django.utils import timezone

from apps.accounts.services import request_password_reset, send_verification_email
from apps.incidents.models import Incident
from apps.notifications.messages import render_error_group, render_incident
from apps.organizations.services import invite_member
from tests.factories import MonitorFactory

pytestmark = pytest.mark.django_db


def assert_routes_to(url: str, view_name: str):
    match = resolve(urlsplit(url).path)
    assert f"web:{match.url_name}" == view_name, url


def test_account_and_invitation_links(owner, organization, run_on_commit, settings):
    settings.SITE_URL = "https://watchgrid.example.com"
    with run_on_commit():
        send_verification_email(owner)
        request_password_reset(owner.email)
        _, accept_url = invite_member(actor=owner, organization=organization, email="new@example.com", role="viewer")
    links = [re.search(r"https://\S+", message.body).group(0) for message in mail.outbox]
    assert_routes_to(links[0], "web:verify-email")
    assert_routes_to(links[1], "web:reset-password")
    assert_routes_to(accept_url, "web:invitation-accept")


def test_alert_links(project):
    now = timezone.now()
    incident = Incident.objects.create(
        project=project,
        monitor=MonitorFactory(project=project),
        title="x",
        detected_at=now,
        confirmed_at=now,
        last_seen_at=now,
    )
    assert_routes_to(render_incident(incident, "monitor_down")["webhook"]["incident"]["url"], "web:incident-detail")

    from apps.errors.models import ErrorGroup

    group = ErrorGroup.objects.create(
        project=project, fingerprint="b" * 64, error_type="E", normalized_message="m", first_seen=now, last_seen=now
    )
    assert_routes_to(render_error_group(group, "error_new")["webhook"]["error_group"]["url"], "web:error-detail")
