"""SSRF controls at configuration time (Section 22.2, SEC-001)."""

import pytest

from common.security.network import TargetBlockedError, is_address_allowed, reset_policy_cache
from common.security.urls import UnsafeURLError, validate_target_url, validate_url_syntax


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.5.4",
        "192.168.0.10",
        "169.254.169.254",  # cloud metadata
        "100.100.100.200",  # Alibaba metadata / CGNAT
        "0.0.0.0",
        "224.0.0.1",
        "255.255.255.255",
        "::1",
        "fe80::1",
        "fd00:ec2::254",  # AWS IPv6 metadata
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "::ffff:10.0.0.1",
        "2002:7f00:1::",  # 6to4 wrapping 127.0.0.1
        "198.18.0.1",
    ],
)
def test_prohibited_addresses_are_blocked(address):
    assert not is_address_allowed(address)


@pytest.mark.parametrize("address", ["93.184.216.34", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses_are_allowed(address):
    assert is_address_allowed(address)


def test_allowlist_permits_explicit_private_range(settings):
    settings.MONITOR_ALLOWED_PRIVATE_NETWORKS = ["127.0.0.1/32"]
    reset_policy_cache()
    assert is_address_allowed("127.0.0.1")
    assert not is_address_allowed("127.0.0.2")
    assert not is_address_allowed("10.0.0.1")


def test_extra_blocklist(settings):
    settings.MONITOR_EXTRA_BLOCKED_NETWORKS = ["93.184.216.0/24"]
    reset_policy_cache()
    assert not is_address_allowed("93.184.216.34")


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/",
        "file:///etc/passwd",
        "gopher://example.com/",
        "javascript:alert(1)",
        "https://user:pass@example.com/",
        "https://example.com/\nX-Injected: 1",
        "https:///nohost",
        "",
    ],
)
def test_url_syntax_rejections(url):
    with pytest.raises(UnsafeURLError):
        validate_url_syntax(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://[::1]:8080/",
        "http://169.254.169.254/latest/meta-data/",
        "https://internal.example.com/",
        "https://metadata.example.com/",
        "https://mixed.example.com/",  # one public and one private record
        "https://rebind.example.com/",
        "https://v6private.example.com/",
        "http://localhost:8000/",
    ],
)
def test_target_validation_blocks_private_destinations(url):
    with pytest.raises(TargetBlockedError):
        validate_target_url(url)


def test_target_validation_accepts_public_and_unresolvable_hosts():
    validate_target_url("https://example.com/health")
    validate_target_url("https://does-not-resolve.invalid/")  # re-checked at connect time
