"""Probe execution against a real local HTTP server (Sections 14.5, 14.6, 22.2)."""

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from apps.checks.probe import ProbeConfig, execute_probe
from apps.monitors.state_machine import OUTCOME_DEGRADED, OUTCOME_FAILURE, OUTCOME_SUCCESS
from common.security.network import TargetBlockedError
from common.security.safe_http import RedirectError, perform_request


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence test output
        pass

    def do_GET(self):
        path = self.path
        if path == "/ok":
            self._send(200, b"service healthy")
        elif path == "/error":
            self._send(500, b"boom")
        elif path == "/slow":
            time.sleep(0.3)
            self._send(200, b"slow but fine")
        elif path == "/hang":
            time.sleep(3)
            self._send(200, b"too late")
        elif path == "/big":
            self._send(200, b"x" * 50_000)
        elif path == "/redirect-ok":
            self._redirect("/ok")
        elif path == "/redirect-private":
            self._redirect("http://10.0.0.1/admin")
        elif path == "/redirect-metadata":
            self._redirect("http://169.254.169.254/latest/meta-data/")
        elif path == "/redirect-loop":
            self._redirect("/redirect-loop")
        elif path == "/redirect-file":
            self._redirect("file:///etc/passwd")
        elif path == "/headers":
            self._send(200, self.headers.get("X-Token", "missing").encode())
        else:
            self._send(404, b"not found")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        self._send(201, b"echo:" + body)

    def _send(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def probe(url, **overrides):
    return execute_probe(ProbeConfig(url=url, timeout_seconds=overrides.pop("timeout_seconds", 2), **overrides))


def test_healthy_endpoint(server, allow_loopback):
    result = probe(f"{server}/ok")
    assert result.outcome == OUTCOME_SUCCESS
    assert result.status_code == 200
    assert result.response_time_ms is not None
    assert result.resolved_ip == "127.0.0.1"


def test_unexpected_status_is_http_failure(server, allow_loopback):
    result = probe(f"{server}/error")
    assert result.outcome == OUTCOME_FAILURE
    assert result.error_category == "http"
    assert result.status_code == 500


def test_expected_status_range_accepts_custom_codes(server, allow_loopback):
    assert probe(f"{server}/error", expected_status="200-299,500").outcome == OUTCOME_SUCCESS


def test_response_time_threshold_marks_degraded(server, allow_loopback):
    result = probe(f"{server}/slow", response_time_threshold_ms=100)
    assert result.outcome == OUTCOME_DEGRADED
    assert result.error_category == "performance"


def test_read_timeout_is_classified(server, allow_loopback):
    result = probe(f"{server}/hang", timeout_seconds=0.5)
    assert result.outcome == OUTCOME_FAILURE
    assert result.error_category == "timeout"


def test_content_assertions(server, allow_loopback):
    assert probe(f"{server}/ok", assertion_mode="contains", assertion_text="healthy").outcome == OUTCOME_SUCCESS
    missing = probe(f"{server}/ok", assertion_mode="contains", assertion_text="database ok")
    assert missing.outcome == OUTCOME_FAILURE and missing.error_category == "content"
    forbidden = probe(f"{server}/ok", assertion_mode="not_contains", assertion_text="healthy")
    assert forbidden.outcome == OUTCOME_FAILURE


def test_response_body_is_capped(server, allow_loopback, settings):
    settings.MONITOR_MAX_RESPONSE_BYTES = 1000
    result = probe(f"{server}/big")
    assert result.outcome == OUTCOME_SUCCESS
    assert result.response_size_bytes == 1000


def test_safe_redirect_is_followed(server, allow_loopback):
    result = probe(f"{server}/redirect-ok")
    assert result.outcome == OUTCOME_SUCCESS
    assert result.redirect_count == 1


@pytest.mark.parametrize("path", ["/redirect-private", "/redirect-metadata", "/redirect-file"])
def test_redirect_to_prohibited_destination_is_blocked(server, allow_loopback, path):
    """SEC-002: a public URL redirecting to a private address never reaches it."""
    with pytest.raises(RedirectError):
        perform_request(f"{server}{path}", timeout_seconds=2)
    result = probe(f"{server}{path}")
    assert result.outcome == OUTCOME_FAILURE
    assert result.error_category == "redirect"


def test_redirect_loop_is_capped(server, allow_loopback):
    result = probe(f"{server}/redirect-loop", max_redirects=3)
    assert result.error_category == "redirect"
    assert "Too many redirects" in result.error_message


def test_redirects_not_followed_when_disabled(server, allow_loopback):
    result = probe(f"{server}/redirect-ok", follow_redirects=False)
    assert result.outcome == OUTCOME_FAILURE
    assert result.status_code == 302


def test_loopback_blocked_without_allowlist(server):
    """SEC-001 at execution time: the connection layer refuses private addresses."""
    with pytest.raises(TargetBlockedError):
        perform_request(f"{server}/ok", timeout_seconds=2)
    result = probe(f"{server}/ok")
    assert result.outcome == OUTCOME_FAILURE
    assert result.error_category == "blocked"


def test_dns_rebinding_host_is_blocked():
    result = probe("http://rebind.example.com/")
    assert result.error_category == "blocked"
    assert result.blocked_address == "127.0.0.1"


def test_dns_failure_is_classified():
    result = probe("https://no-such-host.invalid/")
    assert result.outcome == OUTCOME_FAILURE
    assert result.error_category == "dns"


def test_connection_refused_is_classified(allow_loopback):
    result = probe("http://127.0.0.1:1/")
    assert result.outcome == OUTCOME_FAILURE
    assert result.error_category in {"connection", "timeout"}


def test_custom_headers_and_post_body(server, allow_loopback):
    result = execute_probe(
        ProbeConfig(url=f"{server}/echo", method="POST", body="ping", expected_status="201", timeout_seconds=2)
    )
    assert result.outcome == OUTCOME_SUCCESS
    response = perform_request(f"{server}/headers", headers={"X-Token": "abc", "Host": "evil"}, timeout_seconds=2)
    assert response.text() == "abc"
