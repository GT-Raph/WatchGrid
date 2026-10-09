"""Organizations, membership and invitations (FR-ORG-001, FR-ORG-002)."""

import pytest
from django.core import mail

from apps.notifications.models import AlertPolicy
from apps.organizations.models import Membership
from common.constants import Role
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_create_organization_makes_creator_owner(client_for):
    user = UserFactory()
    client = client_for(user)
    response = client.post("/api/v1/organizations/", {"name": "Kumasi Labs"})
    assert response.status_code == 201
    body = response.json()
    assert body["role"] == "owner" and body["slug"] == "kumasi-labs" and body["member_count"] == 1
    assert Membership.objects.get(user=user).role == Role.OWNER


def test_create_project_creates_default_alert_policy(owner_client, organization):
    response = owner_client.post("/api/v1/projects/", {"organization": str(organization.id), "name": "Billing"})
    assert response.status_code == 201
    policy = AlertPolicy.objects.get(project_id=response.json()["id"])
    assert set(policy.event_types) >= {"monitor_down", "monitor_recovered"}


def test_invitation_accept_flow(owner_client, organization, client_for, run_on_commit):
    with run_on_commit():
        response = owner_client.post(
            f"/api/v1/organizations/{organization.id}/invitations/", {"email": "Dev@Example.com", "role": "developer"}
        )
    assert response.status_code == 201
    accept_url = response.json()["accept_url"]
    assert mail.outbox[-1].to == ["dev@example.com"]
    token = accept_url.split("token=")[1]

    stranger = client_for(UserFactory(email="someone-else@example.com"))
    assert stranger.post("/api/v1/invitations/accept/", {"token": token}).status_code == 403

    invitee = client_for(UserFactory(email="dev@example.com"))
    response = invitee.post("/api/v1/invitations/accept/", {"token": token})
    assert response.status_code == 200
    assert response.json()["role"] == "developer"
    assert invitee.post("/api/v1/invitations/accept/", {"token": token}).status_code == 400  # single use


def test_duplicate_pending_invitation_conflicts(owner_client, organization):
    url = f"/api/v1/organizations/{organization.id}/invitations/"
    assert owner_client.post(url, {"email": "a@example.com"}).status_code == 201
    response = owner_client.post(url, {"email": "a@example.com"})
    assert response.status_code == 409
    assert owner_client.post(url, {"email": "owner@example.com"}).status_code == 409  # already a member


def test_admin_cannot_grant_owner_or_touch_owners(client_for, organization, admin_user, developer, owner):
    admin = client_for(admin_user)
    dev_membership = Membership.objects.get(user=developer)
    owner_membership = Membership.objects.get(user=owner)
    base = f"/api/v1/organizations/{organization.id}/members/"
    assert admin.patch(f"{base}{dev_membership.id}/", {"role": "owner"}).status_code == 403
    assert admin.patch(f"{base}{dev_membership.id}/", {"role": "viewer"}).status_code == 200
    assert admin.delete(f"{base}{owner_membership.id}/").status_code == 403


def test_last_owner_protection(owner_client, organization, owner):
    membership = Membership.objects.get(user=owner)
    base = f"/api/v1/organizations/{organization.id}/members/{membership.id}/"
    assert owner_client.patch(base, {"role": "admin"}).status_code == 403  # primary owner
    assert owner_client.delete(base).status_code == 403


def test_member_can_leave(client_for, organization, viewer):
    membership = Membership.objects.get(user=viewer)
    client = client_for(viewer)
    assert client.delete(f"/api/v1/organizations/{organization.id}/members/{membership.id}/").status_code == 204
    assert client.get(f"/api/v1/organizations/{organization.id}/").status_code == 404


def test_members_list(owner_client, organization, developer):
    body = owner_client.get(f"/api/v1/organizations/{organization.id}/members/").json()
    assert body["count"] == 2
    assert {m["role"] for m in body["results"]} == {"owner", "developer"}


def test_archive_organization_pauses_monitors(owner_client, organization, project):
    from tests.factories import MonitorFactory

    monitor = MonitorFactory(project=project)
    assert owner_client.delete(f"/api/v1/organizations/{organization.id}/").status_code == 204
    monitor.refresh_from_db()
    assert not monitor.is_active
    assert owner_client.get("/api/v1/organizations/").json()["count"] == 0
