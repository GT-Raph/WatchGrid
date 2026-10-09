"""Audit log page (role-filtered, FR-AUD-001)."""

from common.permissions.roles import PROJECT_LEVEL_AUDIT_RESOURCES
from common.permissions.tenancy import has_capability, require_capability
from common.utilities.web import page, paginate, render_page

from .models import AuditLog


@page(methods=("GET",))
def audit(request):
    organization = request.web_scope["organization"]
    require_capability(request.user, organization.pk, "audit.view")
    logs = AuditLog.objects.filter(organization=organization).select_related("actor")
    if not has_capability(request.user, organization.pk, "audit.view_all"):
        logs = logs.filter(resource_type__in=PROJECT_LEVEL_AUDIT_RESOURCES)
    return render_page(request, "audit/list.html", "Audit log", section="settings", rows=paginate(request, logs))
