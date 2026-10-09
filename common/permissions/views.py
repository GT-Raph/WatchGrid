"""Shared view helpers for tenant-scoped resources."""

from __future__ import annotations

from uuid import UUID

from rest_framework.exceptions import NotFound, ValidationError

from .drf import HasActionCapability
from .tenancy import role_for


class TenantScopedMixin:
    """Adds capability-based object permissions and safe parent lookups.

    Subclasses define ``capabilities`` (action → capability) and
    ``organization_lookup`` (attribute path from the object to its organization id).
    """

    capabilities: dict[str, str] = {}
    organization_lookup = "project.organization_id"

    def get_permissions(self):
        return [*super().get_permissions(), HasActionCapability()]

    def get_object_organization_id(self, obj):
        value = obj
        for part in self.organization_lookup.split("."):
            value = getattr(value, part)
        return value

    def role_in(self, organization_id) -> str | None:
        return role_for(self.request.user, organization_id)


def parse_uuid(value, field: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValidationError({field: ["Must be a valid UUID."]}) from exc


def get_scoped_or_404(queryset, pk, field: str = "id"):
    """Fetch from an already tenant-scoped queryset; invalid or foreign ids are 404."""
    try:
        UUID(str(pk))
    except (TypeError, ValueError, AttributeError) as exc:
        raise NotFound() from exc
    obj = queryset.filter(**{field: pk}).first()
    if obj is None:
        raise NotFound()
    return obj
