from django.conf import settings
from django.db import models
from django.db.models import Q

from common.constants import Severity
from common.utilities.models import UUIDModel


class IncidentQuerySet(models.QuerySet):
    def for_user(self, user):
        if not getattr(user, "is_authenticated", False):
            return self.none()
        return self.filter(
            project__organization__memberships__user=user,
            project__organization__archived_at__isnull=True,
        )

    def active(self):
        return self.exclude(status=Incident.Status.RESOLVED)


class Incident(UUIDModel):
    """A confirmed reliability problem tracked through resolution (Section 15)."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ACKNOWLEDGED = "acknowledged", "Acknowledged"
        INVESTIGATING = "investigating", "Investigating"
        MONITORING = "monitoring", "Monitoring"
        RESOLVED = "resolved", "Resolved"

    class Key(models.TextChoices):
        AVAILABILITY = "availability", "Availability"
        PERFORMANCE = "performance", "Performance"
        MANUAL = "manual", "Manual"

    class ResolutionType(models.TextChoices):
        NONE = "", "Unresolved"
        AUTOMATIC = "automatic", "Automatic recovery"
        MANUAL = "manual", "Manual"
        SUPERSEDED = "superseded", "Superseded"

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="incidents")
    monitor = models.ForeignKey(
        "monitors.Monitor", on_delete=models.CASCADE, related_name="incidents", null=True, blank=True
    )
    incident_key = models.CharField(max_length=32, choices=Key.choices, default=Key.AVAILABILITY)
    title = models.CharField(max_length=200)
    severity = models.CharField(max_length=16, choices=Severity.choices, default=Severity.MAJOR)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    failure_type = models.CharField(max_length=32, blank=True, default="")
    summary = models.CharField(max_length=500, blank=True, default="")
    detected_at = models.DateTimeField(help_text="First failed check of the confirmed streak.")
    confirmed_at = models.DateTimeField(help_text="When the confirmation threshold was reached.")
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    resolution_type = models.CharField(max_length=16, choices=ResolutionType.choices, blank=True, default="")
    last_seen_at = models.DateTimeField()
    failure_count = models.PositiveIntegerField(default=0)
    evidence = models.JSONField(default=dict, blank=True)

    objects = IncidentQuerySet.as_manager()

    class Meta:
        ordering = ["-detected_at"]
        constraints = [
            # BR-003 / Section 15.2: database backstop for duplicate prevention.
            models.UniqueConstraint(
                fields=["monitor", "incident_key"],
                condition=~Q(status="resolved") & Q(monitor__isnull=False),
                name="incidents_one_active_per_monitor_key",
            ),
        ]
        indexes = [
            models.Index(fields=["monitor", "status"], name="incident_monitor_status_idx"),
            models.Index(fields=["project", "-detected_at"], name="incident_project_time_idx"),
            models.Index(fields=["status", "-detected_at"], name="incident_status_time_idx"),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def is_active(self) -> bool:
        return self.status != self.Status.RESOLVED

    @property
    def duration_seconds(self) -> int | None:
        from django.utils import timezone

        end = self.resolved_at or timezone.now()
        return int((end - self.detected_at).total_seconds())


class IncidentEvent(models.Model):
    """Timeline entry: state changes, acknowledgements and operator notes."""

    class Kind(models.TextChoices):
        OPENED = "opened", "Opened"
        UPDATED = "updated", "Failure observed"
        STATUS_CHANGED = "status_changed", "Status changed"
        ACKNOWLEDGED = "acknowledged", "Acknowledged"
        NOTE = "note", "Note"
        SEVERITY_CHANGED = "severity_changed", "Severity changed"
        RESOLVED = "resolved", "Resolved"

    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=24, choices=Kind.choices)
    message = models.TextField(blank=True, max_length=5000)
    from_status = models.CharField(max_length=16, blank=True, default="")
    to_status = models.CharField(max_length=16, blank=True, default="")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["incident", "created_at"])]

    def __str__(self) -> str:
        return f"{self.kind} on {self.incident_id}"
