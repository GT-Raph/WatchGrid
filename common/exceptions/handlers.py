"""Consistent error envelope for every API response (Section 20.3)."""

from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import exceptions as drf_exceptions
from rest_framework.response import Response
from rest_framework.views import set_rollback

from . import DomainError

logger = logging.getLogger("watchgrid.api")

_DRF_CODES: dict[type, str] = {
    drf_exceptions.ValidationError: "VALIDATION_ERROR",
    drf_exceptions.ParseError: "MALFORMED_REQUEST",
    drf_exceptions.AuthenticationFailed: "AUTHENTICATION_FAILED",
    drf_exceptions.NotAuthenticated: "AUTHENTICATION_REQUIRED",
    drf_exceptions.PermissionDenied: "PERMISSION_DENIED",
    drf_exceptions.NotFound: "NOT_FOUND",
    drf_exceptions.MethodNotAllowed: "METHOD_NOT_ALLOWED",
    drf_exceptions.NotAcceptable: "NOT_ACCEPTABLE",
    drf_exceptions.UnsupportedMediaType: "UNSUPPORTED_MEDIA_TYPE",
    drf_exceptions.Throttled: "RATE_LIMITED",
}


def _request_id(context) -> str | None:
    request = context.get("request")
    return getattr(request, "request_id", None) if request is not None else None


def _not_found(context) -> tuple[str, str]:
    view = context.get("view")
    model = None
    queryset = getattr(view, "queryset", None)
    if queryset is not None:
        model = queryset.model
    elif view is not None and hasattr(view, "get_queryset"):
        try:
            model = view.get_queryset().model
        except Exception:  # pragma: no cover - defensive; never mask the 404
            model = None
    if model is None:
        return "NOT_FOUND", "The requested resource does not exist."
    name = model._meta.verbose_name
    return f"{model.__name__.upper()}_NOT_FOUND", f"The requested {name} does not exist."


def _envelope(code: str, message: str, details, status_code: int, context, headers=None) -> Response:
    body = {
        "success": False,
        "error": {"code": code, "message": message, "details": details or {}},
        "request_id": _request_id(context),
    }
    return Response(body, status=status_code, headers=headers)


def api_exception_handler(exc, context):
    if isinstance(exc, DomainError):
        set_rollback()
        return _envelope(exc.code, exc.message, exc.details, exc.status_code, context)

    if isinstance(exc, Http404):
        exc = drf_exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = drf_exceptions.PermissionDenied()

    if not isinstance(exc, drf_exceptions.APIException):
        return None  # let Django turn unexpected errors into a 500 (and log them)

    set_rollback()
    headers = {}
    if getattr(exc, "auth_header", None):
        headers["WWW-Authenticate"] = exc.auth_header
    if getattr(exc, "wait", None):
        headers["Retry-After"] = str(int(exc.wait))

    code = next((c for klass, c in _DRF_CODES.items() if isinstance(exc, klass)), "API_ERROR")
    details = {}
    if isinstance(exc, drf_exceptions.ValidationError):
        message = "The submitted data is invalid."
        details = exc.detail if isinstance(exc.detail, dict) else {"non_field_errors": exc.detail}
    elif isinstance(exc, drf_exceptions.NotFound):
        if str(exc.detail) != str(drf_exceptions.NotFound.default_detail):
            message = str(exc.detail)
        else:
            code, message = _not_found(context)
    else:
        message = str(exc.detail) if not isinstance(exc.detail, (dict, list)) else "The request failed."
        if isinstance(exc.detail, dict):
            details = exc.detail

    return _envelope(code, message, details, exc.status_code, context, headers)
