"""SSRF-hardened outbound HTTP client built on urllib3.

Why not a plain ``requests.get``? Validating a host name and then letting the HTTP
library resolve it again opens a DNS-rebinding window: the second lookup can return
a private address. Here the connection class itself resolves the host, validates
every address against :mod:`common.security.network`, and connects to exactly the
address that was validated. TLS SNI and certificate verification still use the
original host name, so HTTPS semantics are unchanged.

Redirects are never followed by urllib3. :func:`perform_request` follows them one
hop at a time, re-validating the scheme, credentials and destination of each hop and
capping the depth. Response bodies are streamed up to a byte limit and a wall-clock
deadline so a hostile target cannot exhaust worker memory or hold it open.
"""

from __future__ import annotations

import socket
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

import certifi
import urllib3
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import (
    ConnectTimeoutError,
    NameResolutionError,
    NewConnectionError,
    ProtocolError,
    ReadTimeoutError,
    SSLError,
)

from .network import TargetBlockedError, resolve_and_validate
from .urls import UnsafeURLError, validate_url_syntax

REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_FORBIDDEN_REQUEST_HEADERS = frozenset({"host", "content-length", "transfer-encoding", "connection", "upgrade"})


@dataclass
class ConnectionContext:
    """Facts captured by the connection layer during one request hop."""

    connected_ip: str | None = None
    tls_version: str | None = None
    tls_not_after: datetime | None = None
    tls_issuer: str | None = None
    tls_subject: str | None = None


class _ValidatingConnectionMixin:
    _wg_context: ConnectionContext

    def _new_conn(self) -> socket.socket:  # type: ignore[override]
        try:
            addresses = resolve_and_validate(self._dns_host, self.port)  # type: ignore[attr-defined]
        except socket.gaierror as exc:
            raise NameResolutionError(self.host, self, exc) from exc  # type: ignore[attr-defined,arg-type]

        last_error: OSError | None = None
        for address in addresses:
            sock = socket.socket(address.family, address.socktype, address.proto)
            try:
                for opt in self.socket_options or ():  # type: ignore[attr-defined]
                    sock.setsockopt(*opt)
                timeout = self.timeout  # type: ignore[attr-defined]
                if isinstance(timeout, (int, float)):
                    sock.settimeout(timeout)
                sock.connect(address.sockaddr)
                self._wg_context.connected_ip = str(address.ip)
                return sock
            except TimeoutError as exc:
                sock.close()
                raise ConnectTimeoutError(self, f"Connection to {self.host} timed out.") from exc  # type: ignore[attr-defined]
            except OSError as exc:
                sock.close()
                last_error = exc
        raise NewConnectionError(self, f"Failed to establish a new connection: {last_error}")  # type: ignore[arg-type]


class SafeHTTPConnection(_ValidatingConnectionMixin, HTTPConnection):
    def __init__(self, *args, wg_context: ConnectionContext, **kwargs):
        self._wg_context = wg_context
        super().__init__(*args, **kwargs)


class SafeHTTPSConnection(_ValidatingConnectionMixin, HTTPSConnection):
    def __init__(self, *args, wg_context: ConnectionContext, **kwargs):
        self._wg_context = wg_context
        super().__init__(*args, **kwargs)

    def connect(self) -> None:
        super().connect()
        sock = self.sock
        if isinstance(sock, ssl.SSLSocket):
            self._wg_context.tls_version = sock.version()
            cert = sock.getpeercert() or {}
            not_after = cert.get("notAfter")
            if not_after:
                self._wg_context.tls_not_after = datetime.fromtimestamp(
                    ssl.cert_time_to_seconds(not_after), tz=timezone.utc
                )
            self._wg_context.tls_issuer = _format_name(cert.get("issuer"))
            self._wg_context.tls_subject = _format_name(cert.get("subject"))


def _format_name(name) -> str | None:
    if not name:
        return None
    parts = []
    for rdn in name:
        for key, value in rdn:
            if key in {"commonName", "organizationName"}:
                parts.append(f"{key}={value}")
    return ", ".join(parts)[:255] or None


class _SafeHTTPPool(HTTPConnectionPool):
    ConnectionCls = SafeHTTPConnection


class _SafeHTTPSPool(HTTPSConnectionPool):
    ConnectionCls = SafeHTTPSConnection


@dataclass
class SafeResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    body_truncated: bool
    elapsed_ms: int
    final_url: str
    redirect_count: int
    connection: ConnectionContext = field(default_factory=ConnectionContext)

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class RedirectError(Exception):
    """Too many redirects or an unsafe redirect destination."""


class DeadlineExceeded(Exception):
    """The overall request deadline elapsed while reading the response."""


class ResponseTooLarge(Exception):
    """Raised only when ``fail_on_oversize`` is requested."""


def _build_pool(scheme: str, host: str, port: int, timeout: urllib3.Timeout, ctx: ConnectionContext):
    if scheme == "https":
        return _SafeHTTPSPool(
            host,
            port,
            timeout=timeout,
            maxsize=1,
            retries=False,
            cert_reqs="CERT_REQUIRED",
            ca_certs=certifi.where(),
            wg_context=ctx,
        )
    return _SafeHTTPPool(host, port, timeout=timeout, maxsize=1, retries=False, wg_context=ctx)


def _request_target(url: str) -> str:
    parts = urlsplit(url)
    target = parts.path or "/"
    if parts.query:
        target = f"{target}?{parts.query}"
    return target


def sanitize_headers(headers: dict[str, str] | None) -> dict[str, str]:
    clean: dict[str, str] = {}
    for key, value in (headers or {}).items():
        name = str(key).strip()
        if not name or name.lower() in _FORBIDDEN_REQUEST_HEADERS:
            continue
        if any(ch in name + str(value) for ch in ("\r", "\n")):
            continue
        clean[name] = str(value)
    return clean


def perform_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout_seconds: float = 10.0,
    follow_redirects: bool = True,
    max_redirects: int = 5,
    max_response_bytes: int = 1024 * 1024,
    user_agent: str = "WatchGrid/1.0",
) -> SafeResponse:
    """Execute one HTTP request (plus validated redirect hops).

    Raises urllib3 / socket exceptions for transport failures, :class:`TargetBlockedError`
    for policy violations, :class:`RedirectError` for redirect problems and
    :class:`DeadlineExceeded` when the overall deadline passes.
    """
    started = time.monotonic()
    deadline = started + timeout_seconds
    current_url = url
    current_method = method.upper()
    current_body = body
    request_headers = sanitize_headers(headers)
    request_headers.setdefault("User-Agent", user_agent)
    request_headers.setdefault("Accept", "*/*")
    redirects = 0

    while True:
        try:
            scheme, host, port = validate_url_syntax(current_url)
        except UnsafeURLError as exc:
            if redirects:
                raise RedirectError(f"Unsafe redirect destination: {exc}") from exc
            raise

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DeadlineExceeded("Request deadline exceeded before connecting.")
        timeout = urllib3.Timeout(connect=remaining, read=remaining)
        ctx = ConnectionContext()
        pool = _build_pool(scheme, host, port, timeout, ctx)
        try:
            try:
                response = pool.urlopen(
                    current_method,
                    _request_target(current_url),
                    body=current_body,
                    headers=request_headers,
                    redirect=False,
                    retries=False,
                    preload_content=False,
                    assert_same_host=False,
                    timeout=timeout,
                )
            except TargetBlockedError as exc:
                if redirects:
                    raise RedirectError(f"Unsafe redirect destination: {exc}") from exc
                raise

            status = response.status
            location = response.headers.get("Location")
            if follow_redirects and status in REDIRECT_STATUSES and location:
                response.drain_conn()
                response.release_conn()
                redirects += 1
                if redirects > max_redirects:
                    raise RedirectError(f"Too many redirects (limit {max_redirects}).")
                current_url = urljoin(current_url, location)
                if status == 303 or (status in (301, 302) and current_method == "POST"):
                    current_method = "GET"
                    current_body = None
                continue

            chunks: list[bytes] = []
            total = 0
            truncated = False
            for chunk in response.stream(16384, decode_content=True):
                if time.monotonic() > deadline:
                    raise DeadlineExceeded("Response body was not received before the deadline.")
                if total + len(chunk) > max_response_bytes:
                    chunks.append(chunk[: max_response_bytes - total])
                    truncated = True
                    break
                chunks.append(chunk)
                total += len(chunk)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            response_headers = {k.lower(): v for k, v in response.headers.items()}
            response.release_conn()
            return SafeResponse(
                status=status,
                headers=response_headers,
                body=b"".join(chunks),
                body_truncated=truncated,
                elapsed_ms=elapsed_ms,
                final_url=current_url,
                redirect_count=redirects,
                connection=ctx,
            )
        finally:
            pool.close()


TRANSPORT_EXCEPTIONS = (
    NameResolutionError,
    ConnectTimeoutError,
    ReadTimeoutError,
    NewConnectionError,
    ProtocolError,
    SSLError,
    socket.timeout,
    OSError,
)
