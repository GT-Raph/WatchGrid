"""Domain exceptions carrying machine-readable error codes (Section 20.3)."""

from __future__ import annotations

from rest_framework import status


class DomainError(Exception):
    """Base for business-rule violations raised from service functions."""

    code = "DOMAIN_ERROR"
    status_code = status.HTTP_400_BAD_REQUEST
    default_message = "The request could not be completed."

    def __init__(self, message: str | None = None, *, code: str | None = None, details: dict | None = None):
        self.message = message or self.default_message
        if code:
            self.code = code
        self.details = details or {}
        super().__init__(self.message)


class ValidationFailed(DomainError):
    code = "VALIDATION_ERROR"


class TargetNotAllowed(DomainError):
    code = "TARGET_NOT_ALLOWED"
    default_message = "The monitoring target is not allowed by network policy."


class InvalidStateTransition(DomainError):
    code = "INVALID_STATE_TRANSITION"
    status_code = status.HTTP_409_CONFLICT
    default_message = "The resource is not in a state that allows this action."


class Conflict(DomainError):
    code = "CONFLICT"
    status_code = status.HTTP_409_CONFLICT


class PermissionDeniedError(DomainError):
    code = "PERMISSION_DENIED"
    status_code = status.HTTP_403_FORBIDDEN
    default_message = "You do not have permission to perform this action."


class ServiceUnavailable(DomainError):
    code = "SERVICE_UNAVAILABLE"
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_message = "The service is temporarily unavailable. Retry later."
