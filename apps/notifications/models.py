from django.conf import settings
from django.db import models

from common.constants import Severity
from common.utilities.models import UUIDModel


class EventType(models.TextChoices):
    MONITOR_DOWN = "monitor_down", "Monitor down"
    MONITOR_DEGRADED = "monitor_degraded", "Monitor degraded"
    MONITOR_RECOVERED = "monitor_recovered", "Monitor recovered"
    ERROR_NEW = "error_new", "New application error"
    ERROR_REGRESSION = "error_regression", "Application error regressed"


class AlertPolicyQuerySet(models.QuerySet):
    def for_user(self, user):
        if not getattr(user, "is_authenticated", False):
            return self.none()
        return self.filter(
            project__organization__memberships__user=user,
            project__organization__archived_at__isnull=True,
        )


class AlertPolicy(UUIDModel):
    """Who is told what, when and how (Section 17.2)."""

    AUDIT_RESOURCE_TYPE = "alert_policy"

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="alert_policies")
    name = models.CharField(max_length=120)
    enabled = models.BooleanField(default=True)
    monitor = models.ForeignKey(
        "monitors.Monitor",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="alert_policies",
        help_text="Limit the policy to one monitor; empty means every monitor in the project.",
    )
    event_types = models.JSONField(default=list)
    min_severity = models.CharField(max_length=16, choices=Severity.choices, default=Severity.INFO)
    delay_seconds = models.PositiveIntegerField(default=0, help_text="Wait before alerting (sustained duration).")
    repeat_interval_minutes = models.PositiveIntegerField(
        default=0, help_text="Remind while the incident stays unresolved; 0 disables reminders."
    )
    # [{"type": "email", "to": [...], "to_roles": [...]}, {"type": "webhook", "url_encrypted": ..., "url_display": ...}]
    channels = models.JSONField(default=list)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    objects = AlertPolicyQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        verbose_name = "alert policy"
        verbose_name_plural = "alert policies"
        indexes = [models.Index(fields=["project", "enabled"])]

    def __str__(self) -> str:
        return self.name


class MaintenanceWindowQuerySet(AlertPolicyQuerySet):
    def active_at(self, moment):
        return self.filter(starts_at__lte=moment, ends_at__gt=moment)


class MaintenanceWindow(UUIDModel):
    """Planned work: checks continue, normal incident alerting is suppressed (BR-006)."""

    AUDIT_RESOURCE_TYPE = "maintenance_window"

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="maintenance_windows")
    monitor = models.ForeignKey(
        "monitors.Monitor", on_delete=models.CASCADE, null=True, blank=True, related_name="maintenance_windows"
    )
    title = models.CharField(max_length=160)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    objects = MaintenanceWindowQuerySet.as_manager()

    class Meta:
        ordering = ["-starts_at"]
        indexes = [models.Index(fields=["project", "starts_at", "ends_at"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F("starts_at")), name="maintenance_valid_range"
            )
        ]


class Notification(UUIDModel):
    """Send ledger: one row per (event, policy, channel, recipient) — Section 17.3."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        SUPPRESSED = "suppressed", "Suppressed"

    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        WEBHOOK = "webhook", "Webhook"

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="notifications")
    incident = models.ForeignKey(
        "incidents.Incident", on_delete=models.CASCADE, null=True, blank=True, related_name="notifications"
    )
    error_group = models.ForeignKey(
        "errors.ErrorGroup", on_delete=models.CASCADE, null=True, blank=True, related_name="notifications"
    )
    policy = models.ForeignKey(AlertPolicy, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    event_type = models.CharField(max_length=32, choices=EventType.choices)
    transition = models.CharField(max_length=16, default="opened")
    escalation_level = models.PositiveSmallIntegerField(default=0)
    channel = models.CharField(max_length=16, choices=Channel.choices)
    recipient = models.CharField(max_length=254, help_text="Email address or masked webhook destination.")
    target_encrypted = models.TextField(blank=True, default="")
    dedupe_key = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    status_reason = models.CharField(max_length=255, blank=True, default="")
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True, default="")
    deliver_after = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["incident", "-created_at"], name="notification_incident_idx"),
            models.Index(fields=["status", "created_at"], name="notification_status_idx"),
            models.Index(fields=["created_at"], name="notification_created_idx"),
        ]
