from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from common.security.crypto import decrypt_json, encrypt_json
from common.security.redaction import REDACTED, is_sensitive_header
from common.utilities.models import UUIDModel


class MonitorQuerySet(models.QuerySet):
    def for_user(self, user):
        if not getattr(user, "is_authenticated", False):
            return self.none()
        return self.filter(
            project__organization__memberships__user=user,
            project__organization__archived_at__isnull=True,
        )

    def not_archived(self):
        return self.filter(archived_at__isnull=True)

    def due(self, now):
        return self.filter(is_active=True, archived_at__isnull=True, next_check_at__lte=now)


class Monitor(UUIDModel):
    """Configuration that defines how a target is checked (Sections 14.4, 19.1)."""

    class Type(models.TextChoices):
        HTTP = "http", "Website (HTTP/HTTPS)"
        API = "api", "API endpoint"
        HEALTH = "health", "Health endpoint"

    class Method(models.TextChoices):
        GET = "GET", "GET"
        HEAD = "HEAD", "HEAD"
        POST = "POST", "POST"
        PUT = "PUT", "PUT"
        PATCH = "PATCH", "PATCH"
        DELETE = "DELETE", "DELETE"
        OPTIONS = "OPTIONS", "OPTIONS"

    class Status(models.TextChoices):
        UNKNOWN = "unknown", "Unknown"
        OPERATIONAL = "operational", "Operational"
        PENDING_FAILURE = "pending_failure", "Pending failure"
        DEGRADED = "degraded", "Degraded"
        DOWN = "down", "Down"
        RECOVERING = "recovering", "Recovering"
        PAUSED = "paused", "Paused"

    class AssertionMode(models.TextChoices):
        NONE = "none", "No content assertion"
        CONTAINS = "contains", "Body contains text"
        NOT_CONTAINS = "not_contains", "Body does not contain text"

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="monitors")
    name = models.CharField(max_length=120)
    monitor_type = models.CharField(max_length=16, choices=Type.choices, default=Type.HTTP)
    url = models.URLField(max_length=2048)
    http_method = models.CharField(max_length=8, choices=Method.choices, default=Method.GET)
    interval_seconds = models.PositiveIntegerField(default=60)
    timeout_seconds = models.PositiveIntegerField(default=10)
    expected_status = models.CharField(
        max_length=120, default="200-299", help_text="Comma-separated codes and ranges, e.g. '200-299,304'."
    )
    response_time_threshold_ms = models.PositiveIntegerField(null=True, blank=True)
    failure_threshold = models.PositiveSmallIntegerField(
        default=3, validators=[MinValueValidator(1), MaxValueValidator(20)]
    )
    recovery_threshold = models.PositiveSmallIntegerField(
        default=2, validators=[MinValueValidator(1), MaxValueValidator(20)]
    )
    follow_redirects = models.BooleanField(default=True)
    max_redirects = models.PositiveSmallIntegerField(default=5, validators=[MaxValueValidator(10)])
    headers_encrypted = models.TextField(blank=True, default="")
    body = models.TextField(blank=True, default="")
    assertion_mode = models.CharField(max_length=16, choices=AssertionMode.choices, default=AssertionMode.NONE)
    assertion_text = models.CharField(max_length=500, blank=True, default="")

    # Runtime state, owned by the monitoring engine.
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.UNKNOWN)
    is_active = models.BooleanField(default=True)
    next_check_at = models.DateTimeField(db_index=True)
    consecutive_failures = models.PositiveIntegerField(default=0)
    consecutive_successes = models.PositiveIntegerField(default=0)
    consecutive_soft_failures = models.PositiveIntegerField(default=0)
    failing_since = models.DateTimeField(null=True, blank=True)
    degraded_since = models.DateTimeField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_status_change_at = models.DateTimeField(null=True, blank=True)
    last_response_time_ms = models.PositiveIntegerField(null=True, blank=True)
    last_status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    last_error_category = models.CharField(max_length=32, blank=True, default="")
    tls_expires_at = models.DateTimeField(null=True, blank=True)

    archived_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    objects = MonitorQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["project", "status"], name="monitor_project_status_idx"),
            models.Index(fields=["is_active", "next_check_at"], name="monitor_due_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "name"],
                condition=models.Q(archived_at__isnull=True),
                name="monitors_unique_active_name_per_project",
            ),
            models.CheckConstraint(
                condition=models.Q(failure_threshold__gte=1) & models.Q(recovery_threshold__gte=1),
                name="monitors_thresholds_positive",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    # -- headers (encrypted at rest, redacted on output) ---------------------------------

    @property
    def headers(self) -> dict[str, str]:
        return decrypt_json(self.headers_encrypted, default={}) or {}

    @headers.setter
    def headers(self, value: dict[str, str] | None) -> None:
        self.headers_encrypted = encrypt_json(value) if value else ""

    @property
    def redacted_headers(self) -> dict[str, str]:
        return {k: (REDACTED if is_sensitive_header(k) else v) for k, v in self.headers.items()}

    @property
    def organization_id(self):
        return self.project.organization_id
