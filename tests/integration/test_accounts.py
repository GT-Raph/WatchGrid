"""Accounts: registration, session login, verification, password recovery (FR-AUTH-*)."""

import re

import pytest
from django.core import mail
from rest_framework.test import APIClient

from apps.accounts.models import User
from tests.factories import PASSWORD, UserFactory

pytestmark = pytest.mark.django_db


def link_from_last_email(pattern: str) -> str:
    match = re.search(pattern, mail.outbox[-1].body)
    assert match, mail.outbox[-1].body
    return match.group(1)


def test_register_logs_in_and_sends_verification(api_client, run_on_commit):
    with run_on_commit():
        response = api_client.post(
            "/api/v1/auth/register/",
            {"email": "New.User@Example.com", "password": "a-Very-long-passphrase-9", "full_name": "New User"},
        )
    assert response.status_code == 201, response.content
    assert response.json()["email"] == "new.user@example.com"
    assert response.json()["email_verified"] is False
    assert api_client.get("/api/v1/auth/me/").status_code == 200

    token = link_from_last_email(r"verify-email/\?token=(\S+)")
    response = api_client.post("/api/v1/auth/verify-email/", {"token": token})
    assert response.status_code == 200 and response.json()["email_verified"] is True


def test_register_rejects_duplicate_and_weak_password(api_client):
    UserFactory(email="taken@example.com")
    response = api_client.post("/api/v1/auth/register/", {"email": "TAKEN@example.com", "password": "x" * 12})
    assert response.status_code == 400
    response = api_client.post("/api/v1/auth/register/", {"email": "weak@example.com", "password": "password"})
    assert response.status_code == 400
    assert "non_field_errors" in response.json()["error"]["details"]


def test_login_logout_flow():
    UserFactory(email="pat@example.com")
    client = APIClient()
    bad = client.post("/api/v1/auth/login/", {"email": "pat@example.com", "password": "wrong"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "INVALID_CREDENTIALS"
    ok = client.post("/api/v1/auth/login/", {"email": "PAT@example.com", "password": PASSWORD})
    assert ok.status_code == 200
    assert client.get("/api/v1/auth/me/").json()["email"] == "pat@example.com"
    assert client.post("/api/v1/auth/logout/").status_code == 204
    assert client.get("/api/v1/auth/me/").status_code == 403


def test_login_is_csrf_protected():
    UserFactory(email="csrf@example.com")
    client = APIClient(enforce_csrf_checks=True)
    response = client.post("/api/v1/auth/login/", {"email": "csrf@example.com", "password": PASSWORD})
    assert response.status_code == 403
    token = client.get("/api/v1/auth/csrf/").json()["csrf_token"]
    response = client.post(
        "/api/v1/auth/login/", {"email": "csrf@example.com", "password": PASSWORD}, HTTP_X_CSRFTOKEN=token
    )
    assert response.status_code == 200


def test_login_throttled_per_account(settings):
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {**settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"], "login": "3/min"},
    }
    from rest_framework.throttling import SimpleRateThrottle

    SimpleRateThrottle.THROTTLE_RATES = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    try:
        UserFactory(email="target@example.com")
        client = APIClient()
        codes = [
            client.post("/api/v1/auth/login/", {"email": "target@example.com", "password": "nope"}).status_code
            for _ in range(5)
        ]
        assert codes[-1] == 429
    finally:
        from django.conf import settings as live

        SimpleRateThrottle.THROTTLE_RATES = live.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]


def test_password_reset_flow_is_single_use(api_client, run_on_commit):
    user = UserFactory(email="forgot@example.com")
    with run_on_commit():
        response = api_client.post("/api/v1/auth/password-reset/", {"email": "forgot@example.com"})
    assert response.status_code == 202
    uid = link_from_last_email(r"uid=([^&\s]+)")
    token = link_from_last_email(r"token=(\S+)")
    new_password = "Another-long-passphrase-7"
    payload = {"uid": uid, "token": token, "new_password": new_password}
    assert api_client.post("/api/v1/auth/password-reset/confirm/", payload).status_code == 204
    user.refresh_from_db()
    assert user.check_password(new_password)
    assert api_client.post("/api/v1/auth/password-reset/confirm/", payload).status_code == 400


def test_password_reset_does_not_reveal_accounts(api_client, run_on_commit):
    with run_on_commit():
        response = api_client.post("/api/v1/auth/password-reset/", {"email": "nobody@example.com"})
    assert response.status_code == 202
    assert not mail.outbox


def test_profile_update_and_password_change(client_for):
    user = UserFactory()
    client = client_for(user)
    assert client.patch("/api/v1/auth/me/", {"full_name": "Renamed", "timezone": "Africa/Accra"}).status_code == 200
    assert client.patch("/api/v1/auth/me/", {"timezone": "Mars/Base"}).status_code == 400
    response = client.post(
        "/api/v1/auth/password-change/", {"current_password": "wrong", "new_password": "Long-enough-passphrase-1"}
    )
    assert response.status_code == 400
    response = client.post(
        "/api/v1/auth/password-change/", {"current_password": PASSWORD, "new_password": "Long-enough-passphrase-1"}
    )
    assert response.status_code == 204
    user.refresh_from_db()
    assert user.check_password("Long-enough-passphrase-1")


def test_email_uniqueness_is_case_insensitive():
    User.objects.create_user(email="Case@Example.com", password="x")
    assert User.objects.get().email == "case@example.com"
