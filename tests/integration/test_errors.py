"""Application error monitoring (Section 16, FR-ERR-001 … 003, ERR-001/002, BR-007/008)."""

import uuid

import pytest
from django.core import mail
from django.utils import timezone

from apps.api_keys.services import create_api_key, revoke_api_key
from apps.errors.models import ErrorEvent, ErrorGroup
from apps.notifications.services import create_default_alert_policy
from tests.factories import ProjectFactory

pytestmark = pytest.mark.django_db

URL = "/api/v1/events/errors/"

PY_TRACE = """Traceback (most recent call last):
  File "/app/payments/views.py", line 41, in create_payment
    charge = gateway.charge(order)
  File "/app/payments/gateway.py", line 88, in charge
    conn = pool.acquire()
DatabaseError: Unable to acquire database connection
"""


@pytest.fixture
def key(project, owner):
    _, raw = create_api_key(actor=owner, project=project, name="backend")
    return raw


def event(**overrides):
    return {
        "environment": "production",
        "level": "error",
        "error_type": "DatabaseError",
        "message": "Unable to acquire database connection",
        "endpoint": "/api/payments/",
        "http_method": "POST",
        "application_version": "2.6.0",
        "user_reference": "user-42",
        "stack_trace": PY_TRACE,
        "metadata": {"request_id": "abc"},
        "occurred_at": timezone.now().isoformat(),
        **overrides,
    }


def post(api_client, key, body, **headers):
    return api_client.post(URL, body, format="json", HTTP_X_PROJECT_KEY=key, **headers)


def test_err_001_valid_event_is_accepted_and_grouped(api_client, key, project, run_on_commit):
    create_default_alert_policy(project)
    with run_on_commit():
        response = post(api_client, key, event())
    assert response.status_code == 202, response.content
    group = ErrorGroup.objects.get(project=project)
    assert group.occurrence_count == 1 and group.affected_users == 1
    assert group.top_frame == "gateway.py:charge"
    assert ErrorEvent.objects.filter(error_group=group).count() == 1
    assert response.json()["fingerprint"] == group.fingerprint
    assert any("NEW ERROR" in m.subject for m in mail.outbox)


def test_err_002_revoked_or_invalid_key_rejected(api_client, key, project, owner):
    from apps.api_keys.models import APIKey

    revoke_api_key(actor=owner, api_key=APIKey.objects.get(project=project))
    response = post(api_client, key, event())
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_FAILED"
    assert post(api_client, "wgk_deadbeef_" + "x" * 43, event()).status_code == 401
    assert api_client.post(URL, event(), format="json").status_code == 401
    assert not ErrorEvent.objects.exists()


def test_bearer_and_body_key_are_accepted(api_client, key, project):
    assert api_client.post(URL, event(), format="json", HTTP_AUTHORIZATION=f"Bearer {key}").status_code == 202
    assert api_client.post(URL, {**event(), "project_key": key}, format="json").status_code == 202


def test_br_007_key_only_writes_to_its_own_project(api_client, key, project, organization):
    other = ProjectFactory(organization=organization)
    post(api_client, key, event())
    assert ErrorGroup.objects.filter(project=project).count() == 1
    assert not ErrorGroup.objects.filter(project=other).exists()


def test_equivalent_errors_with_dynamic_values_share_a_group(api_client, key, project):
    for order_id in (1001, 1002, 1003):
        message = f"Order {order_id} failed for user a{order_id}@example.com at 2026-10-07T14:21:0{order_id % 10}Z"
        post(
            api_client, key, event(message=message, endpoint=f"/api/orders/{order_id}/", user_reference=f"u{order_id}")
        )
    post(api_client, key, event(error_type="TimeoutError", message="Upstream timed out"))
    groups = ErrorGroup.objects.filter(project=project).order_by("first_seen")
    assert groups.count() == 2
    order_group = groups.get(error_type="DatabaseError")
    assert order_group.occurrence_count == 3
    assert order_group.affected_users == 3
    assert order_group.normalized_message == "Order <n> failed for user <email> at <timestamp>"
    assert order_group.endpoint.startswith("/api/orders/")


def test_affected_users_are_distinct_and_pseudonymous(api_client, key, project):
    for ref in ["alice", "alice", "bob", ""]:
        post(api_client, key, event(user_reference=ref))
    group = ErrorGroup.objects.get(project=project)
    assert group.occurrence_count == 4
    assert group.affected_users == 2
    hashes = set(ErrorEvent.objects.values_list("user_reference_hash", flat=True))
    assert "alice" not in hashes and "bob" not in hashes
    assert all(len(h) in (0, 64) for h in hashes)


def test_br_008_sensitive_data_is_redacted(api_client, key, project):
    body = event(
        message="Login failed password=hunter2 token: abcdef123456 card 4111 1111 1111 1111",
        stack_trace=PY_TRACE + "\nAuthorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",
        metadata={"password": "hunter2", "headers": {"Cookie": "session=abc", "X-Ok": "fine"}, "card_number": "4111"},
    )
    assert post(api_client, key, body).status_code == 202
    stored = ErrorEvent.objects.get()
    dump = f"{stored.message} {stored.stack_trace} {stored.metadata}"
    for secret in ["hunter2", "abcdef123456", "4111 1111 1111 1111", "eyJhbGciOiJIUzI1NiJ9", "session=abc"]:
        assert secret not in dump
    assert stored.metadata["headers"]["X-Ok"] == "fine"


def test_idempotent_event_id(api_client, key, project):
    event_id = str(uuid.uuid4())
    for _ in range(3):
        assert post(api_client, key, event(event_id=event_id)).status_code == 202
    assert ErrorGroup.objects.get(project=project).occurrence_count == 1
    assert ErrorEvent.objects.count() == 1


def test_payload_size_and_validation_limits(api_client, key, settings):
    settings.ERROR_EVENT_MAX_BYTES = 2000
    response = post(api_client, key, event(stack_trace="x" * 5000))
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    settings.ERROR_EVENT_MAX_BYTES = 256 * 1024
    response = post(api_client, key, event(level="catastrophic", environment="prod env!"))
    assert response.status_code == 400
    assert {"level", "environment"} <= set(response.json()["error"]["details"])


def test_sampling_keeps_counting_but_stops_storing(api_client, key, project, settings):
    settings.ERROR_EVENT_SAMPLE_THRESHOLD_PER_MINUTE = 3
    responses = [post(api_client, key, event()) for _ in range(5)]
    assert [r.json()["sampled"] for r in responses] == [False, False, False, True, True]
    group = ErrorGroup.objects.get(project=project)
    assert group.occurrence_count == 5
    assert group.stored_event_count == 3


def test_resolved_group_regresses_on_new_event(api_client, key, project, owner_client, run_on_commit):
    create_default_alert_policy(project)
    policy = project.alert_policies.get()
    policy.event_types = ["error_new", "error_regression"]
    policy.save()
    post(api_client, key, event())
    group = ErrorGroup.objects.get(project=project)
    assert owner_client.post(f"/api/v1/errors/{group.id}/resolve/").json()["status"] == "resolved"
    with run_on_commit():
        post(api_client, key, event())
    group.refresh_from_db()
    assert group.status == "unresolved" and group.regression_count == 1
    assert any("REGRESSION" in m.subject for m in mail.outbox)


def test_error_api_lists_and_details(owner_client, api_client, key, project):
    post(api_client, key, event())
    groups = owner_client.get(f"/api/v1/projects/{project.id}/errors/").json()
    assert groups["count"] == 1
    group_id = groups["results"][0]["id"]
    detail = owner_client.get(f"/api/v1/errors/{group_id}/").json()
    assert detail["latest_event"]["stack_trace"].startswith("Traceback")
    assert owner_client.get(f"/api/v1/errors/{group_id}/events/").json()["count"] == 1
    assert owner_client.patch(f"/api/v1/errors/{group_id}/", {"status": "ignored"}).json()["status"] == "ignored"


def test_archived_project_key_stops_working(api_client, key, project):
    project.status = "archived"
    project.save()
    assert post(api_client, key, event()).status_code == 401


def test_api_key_secret_is_never_stored_or_listed(owner_client, project):
    response = owner_client.post(f"/api/v1/projects/{project.id}/api-keys/", {"name": "ci"})
    raw = response.json()["key"]
    from apps.api_keys.models import APIKey

    stored = APIKey.objects.get(pk=response.json()["id"])
    assert raw not in (stored.key_hash, stored.key_prefix)
    listing = owner_client.get(f"/api/v1/projects/{project.id}/api-keys/").content.decode()
    assert raw not in listing


# -- pure helpers ------------------------------------------------------------------------
