"""Ingestion key lifecycle (Section 22.4): generate, display once, hash, revoke."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record_audit
from common.exceptions import InvalidStateTransition
from common.permissions.tenancy import require_capability

from .models import APIKey

KEY_PATTERN = re.compile(r"^(wgk_[0-9a-f]{8})_([A-Za-z0-9_-]{32,})$")
LAST_USED_WRITE_INTERVAL = timedelta(minutes=1)


def _hash(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def generate_raw_key() -> tuple[str, str]:
    prefix = f"wgk_{secrets.token_hex(4)}"
    return prefix, f"{prefix}_{secrets.token_urlsafe(32)}"


def create_api_key(*, actor, project, name: str, scopes: list[str] | None = None, request=None) -> tuple[APIKey, str]:
    require_capability(actor, project.organization_id, "apikey.manage")
    with transaction.atomic():
        while True:
            prefix, raw_key = generate_raw_key()
            if not APIKey.objects.filter(key_prefix=prefix).exists():
                break
        api_key = APIKey.objects.create(
            project=project,
            name=name.strip(),
            key_prefix=prefix,
            key_hash=_hash(raw_key),
            scopes=scopes or [APIKey.Scope.EVENTS_WRITE.value],
            created_by=actor,
        )
        record_audit(
            actor=actor,
            organization=project.organization,
            action="api_key.created",
            resource=api_key,
            metadata={"name": api_key.name, "prefix": prefix, "scopes": api_key.scopes},
            request=request,
        )
    return api_key, raw_key


def revoke_api_key(*, actor, api_key: APIKey, request=None) -> APIKey:
    require_capability(actor, api_key.project.organization_id, "apikey.manage")
    if api_key.revoked_at is not None:
        raise InvalidStateTransition("The key is already revoked.")
    api_key.revoked_at = timezone.now()
    api_key.revoked_by = actor
    api_key.save(update_fields=["revoked_at", "revoked_by", "updated_at"])
    record_audit(
        actor=actor,
        organization=api_key.project.organization,
        action="api_key.revoked",
        resource=api_key,
        metadata={"name": api_key.name, "prefix": api_key.key_prefix},
        request=request,
    )
    return api_key


def authenticate_key(raw_key: str, *, scope: str) -> APIKey | None:
    """Return the active key for ``raw_key`` with ``scope`` or ``None`` (constant-time compare)."""
    match = KEY_PATTERN.match((raw_key or "").strip())
    if not match:
        return None
    api_key = (
        APIKey.objects.select_related("project", "project__organization").filter(key_prefix=match.group(1)).first()
    )
    if api_key is None:
        _hash(raw_key)  # keep timing similar for unknown prefixes
        return None
    if not hmac.compare_digest(api_key.key_hash, _hash(raw_key.strip())):
        return None
    if api_key.revoked_at is not None or scope not in (api_key.scopes or []):
        return None
    project = api_key.project
    if project.status != project.Status.ACTIVE or project.organization.archived_at is not None:
        return None
    now = timezone.now()
    if api_key.last_used_at is None or now - api_key.last_used_at > LAST_USED_WRITE_INTERVAL:
        APIKey.objects.filter(pk=api_key.pk).update(last_used_at=now)
    return api_key
