"""Audit trail recording (FR-AUD-001, Section 22.2 — blocked SSRF attempts)."""

from __future__ import annotations

import logging

from common.security.redaction import redact_data
from common.utilities.context import request_id_var
from common.utilities.network import client_ip

from .models import AuditLog

security_log = logging.getLogger("watchgrid.security")


def _resource_type(resource) -> str:
    return getattr(resource, "AUDIT_RESOURCE_TYPE", None) or resource._meta.model_name


def record_audit(
    *,
    actor=None,
    organization=None,
    action: str,
    resource=None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    metadata: dict | None = None,
    request=None,
) -> AuditLog:
    if resource is not None:
        resource_type = resource_type or _resource_type(resource)
        resource_id = resource_id or str(resource.pk)
    actor = actor if getattr(actor, "is_authenticated", False) else None
    return AuditLog.objects.create(
        organization=organization,
        actor=actor,
        actor_email=getattr(actor, "email", "") or "",
        action=action,
        resource_type=resource_type or "",
        resource_id=resource_id or "",
        metadata=redact_data(metadata or {}),
        ip_address=client_ip(request),
        user_agent=(request.META.get("HTTP_USER_AGENT", "")[:256] if request is not None else ""),
        request_id=(getattr(request, "request_id", None) or request_id_var.get() or "")[:64],
    )


def record_security_event(*, action: str, organization=None, actor=None, metadata: dict | None = None, request=None):
    """Security events go to both the audit table and the security log stream."""
    security_log.warning(action, extra={"security_event": action, "details": redact_data(metadata or {})})
    return record_audit(
        actor=actor,
        organization=organization,
        action=action,
        resource_type="security",
        metadata=metadata,
        request=request,
    )
