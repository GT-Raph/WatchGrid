from django.db import models


class MonitorCheck(models.Model):
    """One probe result (FR-CHK-001). High-volume table: BigAutoField + time indexes."""

    class Kind(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        MANUAL = "manual", "Manual test"

    class Outcome(models.TextChoices):
        SUCCESS = "success", "Success"
        DEGRADED = "degraded", "Degraded"
        FAILURE = "failure", "Failure"
        PLATFORM_ERROR = "platform_error", "Platform error"

    class ErrorCategory(models.TextChoices):
        NONE = "", "None"
        DNS = "dns", "DNS"
        CONNECTION = "connection", "Connection"
        TIMEOUT = "timeout", "Timeout"
        TLS = "tls", "TLS"
        HTTP = "http", "HTTP status"
        REDIRECT = "redirect", "Redirect"
        CONTENT = "content", "Content assertion"
        PERFORMANCE = "performance", "Performance"
        BLOCKED = "blocked", "Blocked by network policy"
        PLATFORM = "platform", "Platform"

    monitor = models.ForeignKey("monitors.Monitor", on_delete=models.CASCADE, related_name="checks")
    execution_key = models.CharField(max_length=64)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.SCHEDULED)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    checked_at = models.DateTimeField()
    outcome = models.CharField(max_length=16, choices=Outcome.choices)
    success = models.BooleanField()
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    response_time_ms = models.PositiveIntegerField(null=True, blank=True)
    error_category = models.CharField(max_length=16, choices=ErrorCategory.choices, blank=True, default="")
    error_message = models.CharField(max_length=500, blank=True, default="")
    timeout_phase = models.CharField(max_length=16, blank=True, default="")
    resolved_ip = models.GenericIPAddressField(null=True, blank=True)
    redirect_count = models.PositiveSmallIntegerField(default=0)
    response_size_bytes = models.PositiveIntegerField(null=True, blank=True)
    tls_expires_at = models.DateTimeField(null=True, blank=True)
    tls_issuer = models.CharField(max_length=255, blank=True, default="")
    monitor_status_after = models.CharField(max_length=32, blank=True, default="")

    class Meta:
        ordering = ["-checked_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["monitor", "execution_key"], name="checks_unique_execution"),
        ]
        indexes = [
            models.Index(fields=["monitor", "-checked_at"], name="check_monitor_time_idx"),
            models.Index(fields=["checked_at"], name="check_time_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.monitor_id} {self.outcome} @ {self.checked_at:%Y-%m-%d %H:%M:%S}"
