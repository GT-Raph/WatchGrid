"""Tenant scoping helpers (Section 23).

Organization is the tenant boundary. Every tenant resource is reached through a
queryset filtered by the requesting user's memberships, so objects in other
organizations are simply invisible (404) rather than forbidden (403). Within an
organization, role capabilities decide what a member may do (403 when refused).
"""

from __future__ import annotations

from uuid import UUID

from rest_framework.exceptions import NotFound, PermissionDenied

from .roles import role_has

_CACHE_ATTR = "_watchgrid_roles"


def _role_cache(user) -> dict:
    cache = getattr(user, _CACHE_ATTR, None)
    if cache is None:
        cache = {}
        setattr(user, _CACHE_ATTR, cache)
    return cache


def clear_role_cache(user) -> None:
    if hasattr(user, _CACHE_ATTR):
        delattr(user, _CACHE_ATTR)


def role_for(user, organization_id: UUID | str | None) -> str | None:
    """Return the user's role in an organization (cached per user object)."""
    if user is None or not getattr(user, "is_authenticated", False) or organization_id is None:
        return None
    key = str(organization_id)
    cache = _role_cache(user)
    if key not in cache:
        from apps.organizations.models import Membership

        cache[key] = (
            Membership.objects.filter(
                user=user, organization_id=organization_id, organization__archived_at__isnull=True
            )
            .values_list("role", flat=True)
            .first()
        )
    return cache[key]


def has_capability(user, organization_id, capability: str) -> bool:
    return role_has(role_for(user, organization_id), capability)


def require_capability(user, organization_id, capability: str, *, hide_existence: bool = True) -> str:
    """Raise 404 for non-members (no metadata leak) and 403 for insufficient role."""
    role = role_for(user, organization_id)
    if role is None:
        if hide_existence:
            raise NotFound()
        raise PermissionDenied()
    if not role_has(role, capability):
        raise PermissionDenied("Your role does not allow this action.")
    return role
