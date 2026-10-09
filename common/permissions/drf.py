"""DRF integration of the capability matrix."""

from __future__ import annotations

from rest_framework.permissions import BasePermission

from .tenancy import has_capability


class HasActionCapability(BasePermission):
    """Object-level check driven by ``view.capabilities``.

    Views declare ``capabilities = {"retrieve": "monitor.view", "partial_update":
    "monitor.manage", ...}`` and implement ``get_object_organization_id(obj)``.
    List/create are scoped by queryset and service checks respectively.
    """

    message = "Your role does not allow this action."

    def has_object_permission(self, request, view, obj) -> bool:
        capability = getattr(view, "capabilities", {}).get(view.action)
        if capability is None:
            return True
        return has_capability(request.user, view.get_object_organization_id(obj), capability)
