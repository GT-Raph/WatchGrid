"""Probe execution and result classification (Sections 14.5, 14.6).

``execute_probe`` never raises for target problems: every transport, TLS, policy or
assertion failure becomes a classified :class:`ProbeResult`. Only bugs in the
platform itself surface as ``PLATFORM_ERROR`` results so they are never blamed on
the monitored service.
"""

from __future__ import annotations

import logging
import socket
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime

from django.conf import settings
from django.utils import timezone
from urllib3.exceptions import (
    ConnectTimeoutError,
    NameResolutionError,
    NewConnectionError,
    ProtocolError,
    ReadTimeoutError,
    SSLError,
)

from apps.monitors.state_machine import OUTCOME_DEGRADED, OUTCOME_FAILURE, OUTCOME_PLATFORM_ERROR, OUTCOME_SUCCESS
from apps.monitors.validators import status_matches
from common.security.network import TargetBlockedError
from common.security.safe_http import DeadlineExceeded, RedirectError, perform_request
from common.security.urls import UnsafeURLError

logger = logging.getLogger("watchgrid.monitoring")

DNS = "dns"
CONNECTION = "connection"
TIMEOUT = "timeout"
TLS = "tls"
HTTP = "http"
REDIRECT = "redirect"
CONTENT = "content"
PERFORMANCE = "performance"
BLOCKED = "blocked"
PLATFORM = "platform"

# Failure categories that mean the service could not be reached at all.
UNREACHABLE_CATEGORIES = frozenset({DNS, CONNECTION, TIMEOUT, TLS, BLOCKED})


@dataclass
class ProbeConfig:
    url: str
    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""
    timeout_seconds: float = 10
    follow_redirects: bool = True
    max_redirects: int = 5
    expected_status: str = "200-299"
    response_time_threshold_ms: int | None = None
    assertion_mode: str = "none"
    assertion_text: str = ""

    @classmethod
    def from_monitor(cls, monitor) -> ProbeConfig:
        return cls(
            url=monitor.url,
            method=monitor.http_method,
            headers=monitor.headers,
            body=monitor.body or "",
            timeout_seconds=min(monitor.timeout_seconds, settings.MONITOR_MAX_TIMEOUT_SECONDS),
            follow_redirects=monitor.follow_redirects,
            max_redirects=min(monitor.max_redirects, settings.MONITOR_MAX_REDIRECTS),
            expected_status=monitor.expected_status,
            response_time_threshold_ms=monitor.response_time_threshold_ms,
            assertion_mode=monitor.assertion_mode,
            assertion_text=monitor.assertion_text,
        )


@dataclass
class ProbeResult:
    outcome: str
    checked_at: datetime
    status_code: int | None = None
    response_time_ms: int | None = None
    error_category: str = ""
    error_message: str = ""
    timeout_phase: str = ""
    resolved_ip: str | None = None
    redirect_count: int = 0
    response_size_bytes: int | None = None
    tls_expires_at: datetime | None = None
    tls_issuer: str = ""
    blocked_address: str | None = None

    @property
    def success(self) -> bool:
        return self.outcome in (OUTCOME_SUCCESS, OUTCOME_DEGRADED)


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _caused_by_timeout(exc: BaseException) -> bool:
    return isinstance(exc.__cause__, socket.timeout) or "timed out" in str(exc).lower()


def classify_exception(exc: BaseException) -> tuple[str, str, str]:
    """Map an exception to ``(category, message, timeout_phase)``."""
    if isinstance(exc, TargetBlockedError):
        return BLOCKED, "Destination blocked by network policy.", ""
    if isinstance(exc, RedirectError):
        return REDIRECT, str(exc), ""
    if isinstance(exc, UnsafeURLError):
        return BLOCKED, str(exc), ""
    if isinstance(exc, (NameResolutionError, socket.gaierror)):
        return DNS, "DNS resolution failed for the target host.", ""
    # NewConnectionError subclasses ConnectTimeoutError in urllib3 2.x: test it first.
    if isinstance(exc, NewConnectionError) and not _caused_by_timeout(exc):
        text = str(exc).lower()
        if "refused" in text:
            return CONNECTION, "Connection refused.", ""
        if "unreachable" in text:
            return CONNECTION, "Network unreachable.", ""
        return CONNECTION, "Failed to establish a connection.", ""
    if isinstance(exc, ConnectTimeoutError):
        return TIMEOUT, "Timed out while connecting.", "connect"
    if isinstance(exc, (ReadTimeoutError, DeadlineExceeded, socket.timeout)):
        return TIMEOUT, "Timed out waiting for the response.", "read"
    if isinstance(exc, (SSLError, ssl.SSLError)):
        reason = getattr(exc, "reason", None) or exc
        text = str(reason)
        if "CERTIFICATE_VERIFY_FAILED" in text or "certificate" in text.lower():
            detail = "certificate verification failed"
            if "expired" in text.lower():
                detail = "certificate has expired"
            elif "hostname" in text.lower() or "match" in text.lower():
                detail = "certificate does not match host name"
            return TLS, f"TLS error: {detail}.", ""
        return TLS, "TLS handshake failed.", ""
    if isinstance(exc, ProtocolError):
        return CONNECTION, "Connection aborted or reset by the server.", ""
    if isinstance(exc, OSError):
        return CONNECTION, f"Connection error ({exc.__class__.__name__}).", ""
    return PLATFORM, f"Internal probe error ({exc.__class__.__name__}).", ""


def execute_probe(config: ProbeConfig) -> ProbeResult:
    checked_at = timezone.now()
    started = time.monotonic()
    body = config.body.encode() if config.body and config.method not in ("GET", "HEAD") else None
    try:
        response = perform_request(
            config.url,
            method=config.method,
            headers=config.headers,
            body=body,
            timeout_seconds=config.timeout_seconds,
            follow_redirects=config.follow_redirects,
            max_redirects=config.max_redirects,
            max_response_bytes=settings.MONITOR_MAX_RESPONSE_BYTES,
            user_agent=settings.MONITOR_USER_AGENT,
        )
    except Exception as exc:  # classified below; nothing escapes as an unhandled error
        category, message, phase = classify_exception(exc)
        if category == PLATFORM:
            logger.exception("probe_platform_error", extra={"url_host": _host(config.url)})
            outcome = OUTCOME_PLATFORM_ERROR
        else:
            outcome = OUTCOME_FAILURE
        timed_out_ms = _elapsed_ms(started)
        return ProbeResult(
            outcome=outcome,
            checked_at=checked_at,
            response_time_ms=timed_out_ms if category in (TIMEOUT, CONNECTION, TLS, HTTP) else None,
            error_category=category,
            error_message=message[:500],
            timeout_phase=phase,
            blocked_address=getattr(exc, "address", None) or getattr(getattr(exc, "__cause__", None), "address", None),
        )

    result = ProbeResult(
        outcome=OUTCOME_SUCCESS,
        checked_at=checked_at,
        status_code=response.status,
        response_time_ms=response.elapsed_ms,
        resolved_ip=response.connection.connected_ip,
        redirect_count=response.redirect_count,
        response_size_bytes=len(response.body),
        tls_expires_at=response.connection.tls_not_after,
        tls_issuer=response.connection.tls_issuer or "",
    )

    if not status_matches(response.status, config.expected_status):
        result.outcome = OUTCOME_FAILURE
        result.error_category = HTTP
        result.error_message = f"Unexpected HTTP status {response.status} (expected {config.expected_status})."
        return result

    if config.assertion_mode in ("contains", "not_contains") and config.assertion_text:
        found = config.assertion_text in response.text()
        if config.assertion_mode == "contains" and not found:
            result.outcome = OUTCOME_FAILURE
            result.error_category = CONTENT
            result.error_message = "Expected text was not found in the response body."
            return result
        if config.assertion_mode == "not_contains" and found:
            result.outcome = OUTCOME_FAILURE
            result.error_category = CONTENT
            result.error_message = "Forbidden text was found in the response body."
            return result

    if config.response_time_threshold_ms and response.elapsed_ms > config.response_time_threshold_ms:
        result.outcome = OUTCOME_DEGRADED
        result.error_category = PERFORMANCE
        result.error_message = (
            f"Response took {response.elapsed_ms} ms (threshold {config.response_time_threshold_ms} ms)."
        )
    return result


def _host(url: str) -> str:
    from urllib.parse import urlsplit

    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""
