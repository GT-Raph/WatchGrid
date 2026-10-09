"""Outbound network policy (Section 22.2 — SSRF protection, mandatory controls).

Every address a monitoring worker may connect to is checked here. The policy is
deny-by-default for anything that is not a globally routable unicast address:
loopback, RFC1918/ULA private ranges, link-local (including the cloud metadata
address 169.254.169.254), carrier-grade NAT, multicast, reserved, unspecified and
documentation ranges are all refused. IPv4-mapped / 6to4 / Teredo IPv6 forms are
unwrapped so an IPv4 private address cannot be smuggled through an IPv6 literal.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from functools import lru_cache

from django.conf import settings

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

# Explicit entries on top of the "not is_global" rule. Most are already non-global;
# listing them documents intent and guards against stdlib classification changes.
_ALWAYS_BLOCKED: tuple[str, ...] = (
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",  # carrier-grade NAT; also Alibaba metadata 100.100.100.200
    "127.0.0.0/8",
    "169.254.0.0/16",  # link-local incl. AWS/GCP/Azure metadata 169.254.169.254
    "172.16.0.0/12",
    "192.0.0.0/24",
    "192.168.0.0/16",
    "198.18.0.0/15",
    "224.0.0.0/4",
    "240.0.0.0/4",
    "255.255.255.255/32",
    "::/128",
    "::1/128",
    "fc00::/7",  # unique local, incl. AWS IPv6 metadata fd00:ec2::254
    "fe80::/10",
    "ff00::/8",
)


class TargetBlockedError(Exception):
    """Raised when a destination violates outbound network policy.

    Deliberately *not* an ``OSError`` subclass so the HTTP client never swallows it
    as an ordinary connection failure.
    """

    def __init__(self, message: str, *, address: str | None = None, host: str | None = None):
        super().__init__(message)
        self.address = address
        self.host = host


@dataclass(frozen=True)
class ResolvedAddress:
    family: int
    socktype: int
    proto: int
    sockaddr: tuple
    ip: IPAddress


@lru_cache(maxsize=1)
def _blocked_networks() -> tuple[IPNetwork, ...]:
    extra = tuple(getattr(settings, "MONITOR_EXTRA_BLOCKED_NETWORKS", ()) or ())
    return tuple(ipaddress.ip_network(cidr, strict=False) for cidr in _ALWAYS_BLOCKED + extra)


@lru_cache(maxsize=1)
def _allowed_private_networks() -> tuple[IPNetwork, ...]:
    allowed = getattr(settings, "MONITOR_ALLOWED_PRIVATE_NETWORKS", ()) or ()
    return tuple(ipaddress.ip_network(cidr, strict=False) for cidr in allowed)


def reset_policy_cache() -> None:
    """Clear cached network lists (used by tests that override settings)."""
    _blocked_networks.cache_clear()
    _allowed_private_networks.cache_clear()


def _unwrap(ip: IPAddress) -> IPAddress:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        if ip.sixtofour is not None:
            return ip.sixtofour
        if ip.teredo is not None:
            return ip.teredo[1]
    return ip


def is_address_allowed(ip: IPAddress | str) -> bool:
    """Return True when an outbound connection to ``ip`` is permitted."""
    if isinstance(ip, str):
        ip = ipaddress.ip_address(ip.split("%", 1)[0])
    candidates = {ip, _unwrap(ip)}
    for candidate in candidates:
        if any(candidate in network for network in _allowed_private_networks()):
            continue
        if any(candidate in network for network in _blocked_networks()):
            return False
        if (
            not candidate.is_global
            or candidate.is_multicast
            or candidate.is_private
            or candidate.is_loopback
            or candidate.is_link_local
            or candidate.is_reserved
            or candidate.is_unspecified
        ):
            return False
    return True


def resolve_host(host: str, port: int) -> list[ResolvedAddress]:
    """Resolve ``host`` with the system resolver (raises ``socket.gaierror``)."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    results: list[ResolvedAddress] = []
    seen: set[tuple] = set()
    for family, socktype, proto, _canon, sockaddr in infos:
        if sockaddr in seen:
            continue
        seen.add(sockaddr)
        ip = ipaddress.ip_address(str(sockaddr[0]).split("%", 1)[0])
        results.append(ResolvedAddress(family, socktype, proto, sockaddr, ip))
    return results


def resolve_and_validate(host: str, port: int) -> list[ResolvedAddress]:
    """Resolve ``host`` and require *every* resolved address to pass policy.

    Blocking the whole destination when any record is private prevents mixed-record
    tricks where one A record is public and another points inside the network.
    """
    addresses = resolve_host(host, port)
    if not addresses:
        raise socket.gaierror(socket.EAI_NONAME, f"No addresses found for {host}")
    for address in addresses:
        if not is_address_allowed(address.ip):
            raise TargetBlockedError(
                f"Destination {host} resolves to a prohibited address range.",
                address=str(address.ip),
                host=host,
            )
    return addresses
