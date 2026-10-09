"""Symmetric encryption for secrets stored at rest (e.g. monitor request headers).

The key comes from ``FIELD_ENCRYPTION_KEY`` (a urlsafe base64 Fernet key). When it is
unset, a key is derived from ``SECRET_KEY`` so development works out of the box;
production deployments should set an explicit key so ``SECRET_KEY`` rotation does
not make stored headers unreadable.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = getattr(settings, "FIELD_ENCRYPTION_KEY", "") or ""
    if not key:
        digest = hashlib.sha256(f"watchgrid-field-encryption:{settings.SECRET_KEY}".encode()).digest()
        key = base64.urlsafe_b64encode(digest).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_json(value) -> str:
    return _fernet().encrypt(json.dumps(value, separators=(",", ":")).encode()).decode()


def decrypt_json(token: str | None, default=None):
    if not token:
        return default
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except (InvalidToken, ValueError):
        return default


def keyed_hash(value: str, *, purpose: str) -> str:
    """Deterministic, non-reversible pseudonym for a value (HMAC-SHA256)."""
    secret = f"watchgrid:{purpose}:{settings.SECRET_KEY}".encode()
    return hmac.new(secret, value.encode(), hashlib.sha256).hexdigest()
