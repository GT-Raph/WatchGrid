"""Client address resolution behind trusted reverse proxies."""

from __future__ import annotations

import ipaddress

from django.conf import settings


def client_ip(request) -> str | None:
    """Return the caller's IP, trusting exactly ``NUM_PROXIES`` X-Forwarded-For hops.

    With no trusted proxies configured the socket peer address is used, so a client
    cannot spoof its address by sending its own X-Forwarded-For header.
    """
    if request is None:
        return None
    proxies = int(settings.REST_FRAMEWORK.get("NUM_PROXIES") or 0)
    candidate = request.META.get("REMOTE_ADDR")
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if proxies and forwarded:
        hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        if hops:
            candidate = hops[-min(proxies, len(hops))]
    try:
        return str(ipaddress.ip_address((candidate or "").strip()))
    except ValueError:
        return None
