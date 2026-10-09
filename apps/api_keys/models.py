from django.conf import settings
from django.db import models

from common.utilities.models import UUIDModel


class APIKeyQuerySet(models.QuerySet):
    def for_user(self, user):
        if not getattr(user, "is_authenticated", False):
            return self.none()
        return self.filter(
            project__organization__memberships__user=user,
            project__organization__archived_at__isnull=True,
        )

    def usable(self):
        return self.filter(revoked_at__isnull=True)


class APIKey(UUIDModel):
    """Project-scoped ingestion credential. Only a prefix and a one-way hash are stored."""

    AUDIT_RESOURCE_TYPE = "api_key"

    class Scope(models.TextChoices):
        EVENTS_WRITE = "events:write", "Submit application error events"

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="api_keys")
    name = models.CharField(max_length=120)
    key_prefix = models.CharField(max_length=16, unique=True)
    key_hash = models.CharField(max_length=64)
    scopes = models.JSONField(default=list)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    objects = APIKeyQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "API key"

    def __str__(self) -> str:
        return f"{self.name} ({self.key_prefix}…)"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None
