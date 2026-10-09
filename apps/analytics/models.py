from django.db import models


class MonitorHourlyStat(models.Model):
    """Hourly rollup kept long after raw checks expire (Section 24.3)."""

    monitor = models.ForeignKey("monitors.Monitor", on_delete=models.CASCADE, related_name="hourly_stats")
    hour = models.DateTimeField()
    total_checks = models.PositiveIntegerField(default=0)
    up_checks = models.PositiveIntegerField(default=0)
    degraded_checks = models.PositiveIntegerField(default=0)
    failed_checks = models.PositiveIntegerField(default=0)
    response_time_sum = models.BigIntegerField(default=0)
    response_time_count = models.PositiveIntegerField(default=0)
    response_time_min = models.PositiveIntegerField(null=True, blank=True)
    response_time_max = models.PositiveIntegerField(null=True, blank=True)
    response_time_p95 = models.PositiveIntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["monitor", "hour"]
        constraints = [models.UniqueConstraint(fields=["monitor", "hour"], name="analytics_unique_monitor_hour")]
        indexes = [models.Index(fields=["hour"], name="hourlystat_hour_idx")]

    def __str__(self) -> str:
        return f"{self.monitor_id} @ {self.hour:%Y-%m-%d %H:00}"
