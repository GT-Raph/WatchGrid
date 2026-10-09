"""Dashboard pages stay compatible with the strict Content-Security-Policy set by Nginx.

``style-src 'self'; script-src 'self'`` forbids inline style attributes, <style> blocks,
inline scripts and event-handler attributes. JSON data blocks (``json_script``) are not
executed and are allowed.
"""

import re

import pytest
from django.test import Client
from django.utils import timezone

from apps.checks.models import MonitorCheck
from apps.incidents.models import Incident
from tests.factories import MonitorFactory

pytestmark = pytest.mark.django_db

INLINE_SCRIPT = re.compile(rb"<script(?![^>]*\bsrc=)(?![^>]*type=\"application/json\")[^>]*>", re.IGNORECASE)
EVENT_HANDLER = re.compile(rb"\son[a-z]+\s*=", re.IGNORECASE)


@pytest.fixture
def pages(owner, project):
    monitor = MonitorFactory(project=project, response_time_threshold_ms=500)
    now = timezone.now()
    for i, outcome in enumerate(["success", "failure", "degraded", "success"]):
        MonitorCheck.objects.create(
            monitor=monitor,
            execution_key=f"s:{i}",
            checked_at=now - timezone.timedelta(minutes=10 - i),
            outcome=outcome,
            success=outcome != "failure",
            response_time_ms=None if outcome == "failure" else 300 + i * 200,
        )
    incident = Incident.objects.create(
        project=project, monitor=monitor, title="Down", detected_at=now, confirmed_at=now, last_seen_at=now
    )
    return ["/", "/login/", f"/monitors/{monitor.pk}/", "/monitors/", f"/incidents/{incident.pk}/", "/reports/"]


def test_pages_have_no_inline_styles_or_scripts(owner, pages):
    client = Client()
    client.force_login(owner)
    for path in pages:
        content = client.get(path).content
        assert b' style="' not in content, path
        assert b"<style" not in content, path
        assert not INLINE_SCRIPT.search(content), path
        assert not EVENT_HANDLER.search(content), path
