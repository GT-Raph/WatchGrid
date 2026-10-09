"""Static validation for user-configured monitoring and webhook URLs."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from .network import TargetBlockedError, is_address_allowed, resolve_and_validate

ALLOWED_SCHEMES = frozenset({"http", "https"})
MAX_URL_LENGTH = 2048


class UnsafeURLError(ValueError):
    """The URL fails scheme/host/credential policy before any network access."""


def validate_url_syntax(url: str) -> tuple[str, str, int]:
    """Check scheme, credentials and host. Returns ``(scheme, host, port)``."""
    if not url or len(url) > MAX_URL_LENGTH:
        raise UnsafeURLError("URL is empty or too long.")
    if any(ch in url for ch in ("\r", "\n", "\t", " ")):
        raise UnsafeURLError("URL must not contain whitespace or control characters.")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise UnsafeURLError("URL is malformed.") from exc
    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeURLError("Only http and https URLs are allowed.")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise UnsafeURLError("URLs with embedded credentials are not allowed; use request headers instead.")
    host = parts.hostname
    if not host:
        raise UnsafeURLError("URL must include a host name.")
    if port is None:
        port = 443 if scheme == "https" else 80
    if port == 0:
        raise UnsafeURLError("URL port is invalid.")
    return scheme, host, port


def _literal_ip(host: str):
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def validate_target_url(url: str, *, resolve: bool = True) -> None:
    """Validate a URL at configuration time.

    Literal IPs are checked directly. Host names are resolved and every address must
    pass policy. Resolution failures are tolerated here — the domain may simply be
    down — because the worker re-validates at connection time anyway.
    """
    _scheme, host, port = validate_url_syntax(url)
    literal = _literal_ip(host)
    if literal is not None:
        if not is_address_allowed(literal):
            raise TargetBlockedError("The URL points at a prohibited address range.", address=str(literal), host=host)
        return
    lowered = host.lower().rstrip(".")
    is_local_name = lowered == "localhost" or lowered.endswith((".localhost", ".internal"))
    if is_local_name and not is_address_allowed("127.0.0.1"):
        raise TargetBlockedError("Local and internal host names are not allowed.", host=host)
    if resolve:
        try:
            resolve_and_validate(host, port)
        except socket.gaierror:
            return
