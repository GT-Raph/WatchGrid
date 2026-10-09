"""Server-rendered workflows, permissions, CSRF, and data isolation."""

from datetime import timedelta

import pytest
from django.contrib.auth.tokens import default_token_generator
from django.test import Client
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.api_keys.models import APIKey
from apps.audit.models import AuditLog
from apps.errors.models import ErrorGroup
from apps.incidents.models import Incident
from apps.monitors.models import Monitor
from apps.notifications.models import AlertPolicy, MaintenanceWindow
from apps.organizations.models import Invitation, Membership, Organization
from apps.projects.models import Project
from tests.factories import PASSWORD, MembershipFactory, MonitorFactory, ProjectFactory, UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def browser(owner, project):
    client = Client()
    client.force_login(owner)
    return client


@pytest.fixture
def incident(project):
    now = timezone.now()
    return Incident.objects.create(
        project=project,
        monitor=MonitorFactory(project=project),
        title="Checkout failed",
        detected_at=now,
        confirmed_at=now,
        last_seen_at=now,
    )


@pytest.fixture
def error_group(project):
    return ErrorGroup.objects.create(
        project=project,
        fingerprint="a" * 64,
        error_type="RuntimeError",
        normalized_message="Payment failed",
        first_seen=timezone.now(),
        last_seen=timezone.now(),
    )


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/projects/",
        "/monitors/",
        "/incidents/",
        "/errors/",
        "/reports/",
        "/team/",
        "/integrations/",
        "/settings/",
        "/settings/audit/",
        "/settings/organization/",
        "/projects/new/",
        "/monitors/new/",
        "/integrations/keys/new/",
        "/integrations/policies/new/",
        "/integrations/maintenance/new/",
        "/team/invite/",
        "/welcome/",
    ],
)
def test_pages_are_django_html(browser, path):
    response = browser.get(path)
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")
    assert response.templates
    assert b"/static/css/app.css" in response.content
    assert b"react" not in response.content.lower()
    assert b"no-store" in response["Cache-Control"].encode()


@pytest.mark.parametrize("path", ["/login/", "/register/", "/forgot-password/", "/reset-password/", "/verify-email/"])
def test_public_pages(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert b"csrfmiddlewaretoken" in response.content


def test_login_safe_redirect_and_logout(client, owner, project):
    response = client.get("/monitors/")
    assert response.status_code == 302 and "/login/?next=" in response.url
    response = client.post("/login/", {"email": owner.email, "password": PASSWORD, "next": "https://evil.example/"})
    assert response.status_code == 302 and response.url == "/"
    assert client.get("/logout/").status_code == 405
    assert client.post("/logout/").status_code == 302
    assert client.get("/").status_code == 302


def test_csrf_required_for_login_and_mutations(owner, project):
    client = Client(enforce_csrf_checks=True)
    assert client.post("/login/", {"email": owner.email, "password": PASSWORD}).status_code == 403
    client.get("/login/")
    token = client.cookies["watchgrid_csrftoken"].value
    assert (
        client.post("/login/", {"email": owner.email, "password": PASSWORD, "csrfmiddlewaretoken": token}).status_code
        == 302
    )
    monitor = MonitorFactory(project=project)
    assert client.post(f"/monitors/{monitor.pk}/pause/").status_code == 403
    monitor.refresh_from_db()
    assert monitor.is_active


def test_registration_onboarding_and_project(client):
    response = client.post("/register/", {"email": "new@example.com", "password": PASSWORD, "full_name": "New Owner"})
    assert response.status_code == 302
    assert client.get("/").url == "/welcome/"
    assert client.post("/welcome/", {"name": "New organization"}).url == "/projects/"
    org = Organization.objects.get(name="New organization")
    response = client.post(
        "/projects/new/", {"organization": str(org.pk), "name": "Production", "environment": "production"}
    )
    assert response.status_code == 302
    project = Project.objects.get(organization=org)
    assert project.alert_policies.count() == 1


def test_invalid_forms_preserve_input(browser, project):
    response = browser.post(
        "/monitors/new/", {"project": str(project.pk), "name": "My API", "url": "http://127.0.0.1/"}
    )
    assert response.status_code == 200
    assert response.context["form"].errors
    assert b"My API" in response.content
    assert not Monitor.objects.exists()


def monitor_payload(project, **overrides):
    return {
        "project": str(project.pk),
        "name": "API health",
        "url": "https://example.com/health",
        "monitor_type": "http",
        "http_method": "GET",
        "interval_seconds": "60",
        "timeout_seconds": "10",
        "expected_status": "200-299",
        "failure_threshold": "3",
        "recovery_threshold": "2",
        "follow_redirects": "on",
        "max_redirects": "5",
        "assertion_mode": "none",
        **overrides,
    }


def test_monitor_lifecycle(browser, project, monkeypatch):
    response = browser.post("/monitors/new/", monitor_payload(project))
    assert response.status_code == 302, getattr(response, "context", None)
    monitor = Monitor.objects.get()
    assert AuditLog.objects.filter(action="monitor.created").exists()
    detail = browser.get(response.url)
    assert detail.status_code == 200 and b"Check history" in detail.content
    edit = browser.post(f"/monitors/{monitor.pk}/edit/", monitor_payload(project, name="Renamed"))
    assert edit.status_code == 302
    assert browser.post(f"/monitors/{monitor.pk}/pause/").status_code == 302
    monitor.refresh_from_db()
    assert not monitor.is_active
    assert browser.post(f"/monitors/{monitor.pk}/resume/").status_code == 302
    monitor.refresh_from_db()
    assert monitor.is_active
    calls = []
    monkeypatch.setattr("apps.checks.tasks.run_manual_check.apply_async", lambda **kw: calls.append(kw))
    assert browser.post(f"/monitors/{monitor.pk}/test/").status_code == 302
    assert calls
    assert browser.get(f"/monitors/{monitor.pk}/archive/").status_code == 200
    monitor.refresh_from_db()
    assert monitor.archived_at is None
    assert browser.post(f"/monitors/{monitor.pk}/archive/", {"confirm": "on"}).status_code == 302
    monitor.refresh_from_db()
    assert monitor.archived_at is not None


def test_private_dns_rejected(browser, project):
    response = browser.post("/monitors/new/", monitor_payload(project, url="https://internal.example.com/"))
    assert response.status_code == 200
    assert "url" in response.context["form"].errors
    assert not Monitor.objects.exists()


def test_monitor_headers_are_redacted(browser, project):
    monitor = MonitorFactory(project=project)
    monitor.headers = {"Authorization": "Bearer never-show-this"}
    monitor.save()
    response = browser.get(f"/monitors/{monitor.pk}/edit/")
    assert b"never-show-this" not in response.content
    assert b"REDACTED" in response.content


@pytest.mark.parametrize(
    "path",
    [
        "/projects/new/",
        "/monitors/new/",
        "/team/invite/",
        "/integrations/keys/new/",
        "/integrations/policies/new/",
        "/integrations/maintenance/new/",
        "/settings/organization/",
        "/settings/audit/",
    ],
)
def test_viewer_cannot_manage(client, viewer, project, path):
    client.force_login(viewer)
    assert client.get(path).status_code == 403
    assert client.post(path, {"name": "Unauthorized"}).status_code == (405 if path == "/settings/audit/" else 403)


def test_cross_tenant_resources_hidden(browser, project):
    foreign = MonitorFactory()
    assert browser.get(f"/monitors/{foreign.pk}/").status_code == 404
    assert browser.post(f"/monitors/{foreign.pk}/pause/").status_code == 404
    assert browser.get("/", {"organization": str(foreign.project.organization_id)}).status_code == 404
    assert browser.get("/", {"project": str(foreign.project_id)}).status_code == 404
    assert browser.get("/", {"organization": "invalid"}).status_code == 404


def test_incident_notes_status_and_viewer_restriction(browser, incident, viewer):
    url = f"/incidents/{incident.pk}/"
    assert browser.get(url).status_code == 200
    assert browser.post(url, {"action": "note", "message": "Investigating the upstream service"}).status_code == 302
    assert incident.events.filter(message="Investigating the upstream service").exists()
    assert browser.post(url, {"action": "status", "status": "acknowledged", "note": "On it"}).status_code == 302
    incident.refresh_from_db()
    assert incident.status == "acknowledged"
    browser.force_login(viewer)
    assert browser.post(url, {"action": "note", "message": "Unauthorized"}).status_code == 403


def test_error_status(browser, error_group):
    url = f"/errors/{error_group.pk}/"
    assert browser.get(url).status_code == 200
    assert browser.post(url, {"status": "resolved"}).status_code == 302
    error_group.refresh_from_db()
    assert error_group.status == "resolved"


def test_project_key_shown_once(browser, project):
    response = browser.post("/integrations/keys/new/", {"project": str(project.pk), "name": "Production"})
    assert response.status_code == 200
    key = APIKey.objects.get()
    secret = response.context["raw_key"]
    assert secret.encode() in response.content
    assert secret.encode() not in browser.get("/integrations/").content
    assert browser.post(f"/integrations/keys/{key.pk}/revoke/", {"confirm": "on"}).status_code == 302
    key.refresh_from_db()
    assert key.revoked_at is not None


def test_alert_policy_and_maintenance(browser, project):
    payload = {
        "project": str(project.pk),
        "name": "Ops",
        "enabled": "on",
        "monitor": "",
        "event_types": ["monitor_down", "monitor_recovered"],
        "min_severity": "info",
        "recipients": "ops@example.com",
        "delay_seconds": "0",
        "repeat_interval_minutes": "0",
    }
    response = browser.post("/integrations/policies/new/", payload)
    assert response.status_code == 302, response.context["form"].errors
    policy = AlertPolicy.objects.get(name="Ops")
    assert browser.get(f"/integrations/policies/{policy.pk}/edit/").status_code == 200
    payload["name"] = "Updated ops"
    assert browser.post(f"/integrations/policies/{policy.pk}/edit/", payload).status_code == 302
    start = timezone.now() + timedelta(hours=1)
    payload = {
        "project": str(project.pk),
        "title": "Database upgrade",
        "monitor": "",
        "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(hours=1)).isoformat(),
    }
    response = browser.post("/integrations/maintenance/new/", payload)
    assert response.status_code == 302, response.context["form"].errors
    window = MaintenanceWindow.objects.get()
    assert browser.get(f"/integrations/maintenance/{window.pk}/edit/").status_code == 200
    assert browser.post(f"/integrations/maintenance/{window.pk}/delete/", {"confirm": "on"}).status_code == 302
    assert not MaintenanceWindow.objects.exists()


def test_invitation_and_role(browser, organization):
    assert browser.post("/team/invite/", {"email": "teammate@example.com", "role": "viewer"}).status_code == 302
    invitation = Invitation.objects.get(email="teammate@example.com")
    assert browser.post(f"/team/{invitation.pk}/revoke-invitation/", {"confirm": "on"}).status_code == 302
    member = MembershipFactory(organization=organization, role="viewer")
    assert browser.post(f"/team/{member.pk}/role/", {"role": "developer"}).status_code == 302
    member.refresh_from_db()
    assert member.role == "developer"
    assert browser.post(f"/team/{member.pk}/remove/", {"confirm": "on"}).status_code == 302
    assert not Membership.objects.filter(pk=member.pk).exists()


def test_profile_password_and_recovery(browser, owner, client):
    response = browser.post("/settings/", {"action": "profile", "full_name": "Operator", "timezone": "Africa/Accra"})
    assert response.status_code == 302
    owner.refresh_from_db()
    assert owner.full_name == "Operator"
    assert (
        browser.post(
            "/settings/", {"action": "password", "current_password": PASSWORD, "new_password": "Changed-Passw0rd-123!"}
        ).status_code
        == 302
    )
    assert browser.get("/").status_code == 200
    owner.refresh_from_db()
    assert owner.check_password("Changed-Passw0rd-123!")
    token = default_token_generator.make_token(owner)
    response = client.post(
        "/reset-password/",
        {"uid": urlsafe_base64_encode(force_bytes(owner.pk)), "token": token, "new_password": "Reset-Passw0rd-123!"},
    )
    assert response.status_code == 302
    owner.refresh_from_db()
    assert owner.check_password("Reset-Passw0rd-123!")


def test_csv_escapes_formulas_and_scope(browser, project):
    MonitorFactory(project=project, name="=DANGER()")
    foreign = MonitorFactory(name="Foreign secret")
    response = browser.get("/reports/?format=csv")
    assert response.status_code == 200
    assert b"'=DANGER()" in response.content
    assert foreign.name.encode() not in response.content


def test_selected_project_filters_overview(browser, project):
    other = ProjectFactory(organization=project.organization)
    MonitorFactory(project=project, name="Chosen monitor")
    MonitorFactory(project=other, name="Other monitor")
    response = browser.get("/", {"project": str(project.pk)})
    assert response.context["summary"]["monitors"]["total"] == 1
    assert b"Chosen monitor" in response.content
    assert b"Other monitor" not in response.content


def test_empty_and_unknown_routes(client, browser):
    assert browser.get("/not-a-page/").status_code == 404
    assert browser.get("/", {"range": "invalid"}).status_code == 400
    user = UserFactory()
    client.force_login(user)
    assert client.get("/").url == "/welcome/"
