import uuid

from django.conf import settings
from django.db import models

from common.utilities.models import UUIDModel


class ErrorGroupQuerySet(models.QuerySet):
    def for_user(self, user):
        if not getattr(user, "is_authenticated", False):
            return self.none()
        return self.filter(
            project__organization__memberships__user=user,
            project__organization__archived_at__isnull=True,
        )


class ErrorGroup(UUIDModel):
    """Aggregate of equivalent error events sharing a fingerprint (Section 16.2)."""

    AUDIT_RESOURCE_TYPE = "error_group"

    class Status(models.TextChoices):
        UNRESOLVED = "unresolved", "Unresolved"
        RESOLVED = "resolved", "Resolved"
        IGNORED = "ignored", "Ignored"

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="error_groups")
    fingerprint = models.CharField(max_length=64)
    fingerprint_version = models.CharField(max_length=8, default="v1")
    environment = models.CharField(max_length=64, default="production")
    level = models.CharField(max_length=16, default="error")
    error_type = models.CharField(max_length=200)
    normalized_message = models.CharField(max_length=1000)
    sample_message = models.TextField(max_length=2000, blank=True, default="")
    endpoint = models.CharField(max_length=500, blank=True, default="")
    http_method = models.CharField(max_length=10, blank=True, default="")
    top_frame = models.CharField(max_length=500, blank=True, default="")
    first_seen = models.DateTimeField()
    last_seen = models.DateTimeField()
    occurrence_count = models.BigIntegerField(default=0)
    stored_event_count = models.BigIntegerField(default=0)
    affected_users = models.PositiveIntegerField(default=0)
    last_release = models.CharField(max_length=100, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.UNRESOLVED)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    regression_count = models.PositiveIntegerField(default=0)

    objects = ErrorGroupQuerySet.as_manager()

    class Meta:
        ordering = ["-last_seen"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "fingerprint", "environment"], name="errors_unique_group_fingerprint"
            )
        ]
        indexes = [
            models.Index(fields=["project", "environment", "-last_seen"], name="errorgroup_project_seen_idx"),
            models.Index(fields=["project", "status", "-last_seen"], name="errorgroup_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.error_type}: {self.normalized_message[:60]}"


class ErrorEvent(models.Model):
    """One stored occurrence. High volume: payload caps and retention apply (Section 16.4)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    error_group = models.ForeignKey(ErrorGroup, on_delete=models.CASCADE, related_name="events")
    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="+")
    occurred_at = models.DateTimeField()
    received_at = models.DateTimeField()
    level = models.CharField(max_length=16, default="error")
    message = models.TextField(max_length=2000)
    endpoint = models.CharField(max_length=500, blank=True, default="")
    http_method = models.CharField(max_length=10, blank=True, default="")
    application_version = models.CharField(max_length=100, blank=True, default="")
    user_reference_hash = models.CharField(max_length=64, blank=True, default="")
    stack_trace = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["error_group", "-occurred_at"], name="errorevent_group_time_idx"),
            models.Index(fields=["received_at"], name="errorevent_received_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.error_group_id} @ {self.occurred_at:%Y-%m-%d %H:%M:%S}"


class ErrorGroupUser(models.Model):
    """Distinct pseudonymous users affected by a group (FR-ERR-003)."""

    error_group = models.ForeignKey(ErrorGroup, on_delete=models.CASCADE, related_name="affected_user_refs")
    user_hash = models.CharField(max_length=64)
    first_seen = models.DateTimeField()
    last_seen = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["error_group", "user_hash"], name="errors_unique_group_user")]

    def __str__(self) -> str:
        return f"{self.error_group_id}:{self.user_hash[:8]}"
