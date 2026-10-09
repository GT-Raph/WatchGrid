import ipaddress
import socket

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from common.constants import Role
from common.security import network
from common.security.crypto import _fernet

from .factories import MembershipFactory, OrganizationFactory, ProjectFactory, UserFactory

# Deterministic DNS for tests: no real network lookups ever happen.
FAKE_DNS = {
    "example.com": ["93.184.216.34"],
    "api.example.com": ["93.184.216.35"],
    "status.example.org": ["93.184.216.36"],
    "hooks.example.com": ["93.184.216.40"],
    "internal.example.com": ["10.0.0.5"],
    "metadata.example.com": ["169.254.169.254"],
    "mixed.example.com": ["93.184.216.37", "192.168.1.10"],
    "rebind.example.com": ["127.0.0.1"],
    "v6private.example.com": ["fd00::1"],
}


def fake_resolve_host(host: str, port: int):
    try:
        ips = [str(ipaddress.ip_address(host.strip("[]")))]
    except ValueError:
        ips = FAKE_DNS.get(host.lower().rstrip("."))
    if not ips:
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
    results = []
    for ip in ips:
        addr = ipaddress.ip_address(ip)
        family = socket.AF_INET6 if addr.version == 6 else socket.AF_INET
        sockaddr = (ip, port, 0, 0) if addr.version == 6 else (ip, port)
        results.append(network.ResolvedAddress(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, sockaddr, addr))
    return results


@pytest.fixture(autouse=True)
def _isolation(monkeypatch, settings):
    monkeypatch.setattr(network, "resolve_host", fake_resolve_host)
    network.reset_policy_cache()
    _fernet.cache_clear()
    cache.clear()
    yield
    network.reset_policy_cache()


@pytest.fixture
def allow_loopback(settings):
    """Permit 127.0.0.1 targets so probes can hit a local test server."""
    settings.MONITOR_ALLOWED_PRIVATE_NETWORKS = ["127.0.0.1/32"]
    network.reset_policy_cache()
    yield
    network.reset_policy_cache()


@pytest.fixture
def owner(db):
    return UserFactory(email="owner@example.com")


@pytest.fixture
def organization(owner):
    return OrganizationFactory(owner=owner, name="Acme", slug="acme")


@pytest.fixture
def project(organization):
    return ProjectFactory(organization=organization, name="Storefront", slug="storefront")


def member(organization, role):
    return MembershipFactory(organization=organization, role=role).user


@pytest.fixture
def admin_user(organization):
    return member(organization, Role.ADMIN)


@pytest.fixture
def developer(organization):
    return member(organization, Role.DEVELOPER)


@pytest.fixture
def viewer(organization):
    return member(organization, Role.VIEWER)


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def client_for():
    def _make(user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    return _make


@pytest.fixture
def owner_client(client_for, owner):
    return client_for(owner)


@pytest.fixture
def run_on_commit(django_capture_on_commit_callbacks):
    """Execute ``transaction.on_commit`` callbacks (Celery is eager in tests)."""

    def _ctx():
        return django_capture_on_commit_callbacks(execute=True)

    return _ctx
