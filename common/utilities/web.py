"""Shared plumbing for the server-rendered dashboard (Django templates).

Every HTML view is wrapped by :func:`page`, which enforces authentication, applies the
viewer's time zone (BR-010), resolves the organization/project scope from the session
and renders domain errors as friendly pages. Views stay thin: they validate input with
the same serializers as the REST API and call the same service functions.
"""

from __future__ import annotations

from functools import wraps
from types import SimpleNamespace
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib.auth.views import redirect_to_login
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from rest_framework.exceptions import APIException, Throttled
from rest_framework.throttling import ScopedRateThrottle

from common.exceptions import DomainError
from common.permissions.roles import CAPABILITIES
from common.permissions.tenancy import has_capability

from .forms import error_text

# (url name, label, icon)
NAVIGATION = [
    ("overview", "Overview", "grid"),
    ("projects", "Projects", "folder"),
    ("monitors", "Monitors", "pulse"),
    ("incidents", "Incidents", "alert"),
    ("errors", "Errors", "code"),
    ("reports", "Reports", "chart"),
    ("team", "Team", "users"),
    ("integrations", "Integrations", "link"),
    ("settings", "Settings", "settings"),
]

RANGES = [
    ("1h", "Last hour"),
    ("24h", "Last 24 hours"),
    ("7d", "Last 7 days"),
    ("30d", "Last 30 days"),
    ("90d", "Last 90 days"),
]


def uuid_value(value) -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise Http404 from None


def resolve_scope(request) -> dict | None:
    """Organization and project the dashboard shows, remembered in the session.

    Returns ``None`` when the user belongs to no organization yet.
    """
    from apps.organizations.models import Organization
    from apps.projects.models import Project

    organizations = Organization.objects.for_user(request.user).order_by("name")
    explicit_org = request.GET.get("organization")
    remembered = explicit_org or request.session.get("organization")
    if explicit_org:
        organization = get_object_or_404(organizations, pk=uuid_value(explicit_org))
    else:
        organization = organizations.filter(pk=uuid_value(remembered)).first() if remembered else None
        organization = organization or organizations.first()
    if organization is None:
        request.session.pop("organization", None)
        request.session.pop("project", None)
        return None

    changed = str(organization.pk) != request.session.get("organization")
    request.session["organization"] = str(organization.pk)
    projects = (
        Project.objects.for_user(request.user).filter(organization=organization, status="active").order_by("name")
    )
    project_id = request.GET.get("project", "" if changed else request.session.get("project", ""))
    project = projects.filter(pk=uuid_value(project_id)).first() if project_id else None
    if project_id and project is None and "project" in request.GET and not changed:
        raise Http404
    # Switching organization may submit a project from the previous one, and archived
    # projects or role changes can invalidate a remembered selection: both reset cleanly.
    request.session["project"] = str(project.pk) if project else ""
    return {
        "organization": organization,
        "organizations": organizations,
        "projects": projects,
        "project": project,
        "capabilities": {
            name.replace(".", "_"): has_capability(request.user, organization.pk, name) for name in CAPABILITIES
        },
    }


def page(*, public: bool = False, organization: bool = True, methods=("GET", "POST")):
    """Decorate an HTML view with authentication, time zone, scope and error pages."""

    def decorate(view):
        @never_cache
        @require_http_methods(methods)
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not public and not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path(), login_url="web:login")
            try:
                zone = ZoneInfo(getattr(request.user, "timezone", "UTC") or "UTC")
            except ZoneInfoNotFoundError:
                zone = ZoneInfo("UTC")
            try:
                with timezone.override(zone):
                    request.web_scope = resolve_scope(request) if organization and not public else {}
                    if request.web_scope is None:
                        return redirect("web:welcome")
                    return view(request, *args, **kwargs)
            except (DomainError, APIException) as exc:
                return render(
                    request,
                    "error.html",
                    {
                        "title": "Unable to complete request",
                        "error": exc.message if isinstance(exc, DomainError) else error_text(exc.detail),
                        "status_code": exc.status_code,
                    },
                    status=exc.status_code,
                )

        return wrapped

    return decorate


def render_page(request, template: str, title: str, **context):
    """Render a dashboard page with the shared layout context."""
    section = context.pop("section", request.resolver_match.url_name.split("-")[0])
    return render(
        request,
        template,
        {
            **(getattr(request, "web_scope", {}) or {}),
            "title": title,
            "section": section,
            "navigation": NAVIGATION,
            "snapshot_at": timezone.now(),
            "ranges": RANGES,
            "selected_range": request.GET.get("range", "24h"),
            **context,
        },
    )


def scoped(request, model):
    """Tenant-scoped queryset narrowed to the selected organization and project."""
    queryset = model.objects.for_user(request.user).filter(project__organization=request.web_scope["organization"])
    if request.web_scope["project"]:
        queryset = queryset.filter(project=request.web_scope["project"])
    return queryset


def paginate(request, queryset, size: int = 25):
    return Paginator(queryset, size).get_page(request.GET.get("page"))


def throttle(request, name: str) -> None:
    """Apply the API's named rate limit to a form post."""
    from apps.accounts.throttles import LoginEmailThrottle

    request.data = request.POST
    view = SimpleNamespace(throttle_scope=name)
    limiters = [ScopedRateThrottle()]
    if name == "login":
        limiters.append(LoginEmailThrottle())
    for limiter in limiters:
        if not limiter.allow_request(request, view):
            raise Throttled(wait=limiter.wait())
