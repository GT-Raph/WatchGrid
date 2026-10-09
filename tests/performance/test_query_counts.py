"""Query-count budgets for the busiest pages and endpoints (Section 31.1, NFR-PERF-001).

A page that issues one query per monitor is fine with five monitors and unusable with
five thousand. These tests pin each view to a fixed budget and check it does not grow
when the number of monitors grows.
"""

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.checks.models import MonitorCheck
from tests.factories import MonitorFactory

pytestmark = pytest.mark.django_db

PAGES = ["/", "/monitors/", "/reports/", "/api/v1/monitors/", "/api/v1/dashboard/", "/api/v1/dashboard/grid/"]


def seed(project, count):
    monitors = MonitorFactory.create_batch(count, project=project)
    now = timezone.now()
    MonitorCheck.objects.bulk_create(
        [
            MonitorCheck(
                monitor=monitor,
                execution_key=f"s:{i}",
                checked_at=now - timezone.timedelta(minutes=i + 1),
                outcome="success",
                success=True,
                response_time_ms=100,
            )
            for monitor in monitors
            for i in range(5)
        ]
    )


def queries_for(client, path):
    with CaptureQueriesContext(connection) as captured:
        response = client.get(path)
    assert response.status_code == 200, path
    return len(captured)


@pytest.mark.parametrize("path", PAGES)
def test_query_count_does_not_grow_with_monitors(owner, project, path):
    client = Client()
    client.force_login(owner)
    seed(project, 3)
    small = queries_for(client, path)
    seed(project, 20)
    large = queries_for(client, path)
    assert large <= small, f"{path}: {small} queries with 3 monitors, {large} with 23"
    assert large <= 40, f"{path} issues {large} queries"
