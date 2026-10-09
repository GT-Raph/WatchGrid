from django.contrib import admin

from .models import Monitor


@admin.register(Monitor)
class MonitorAdmin(admin.ModelAdmin):
    list_display = ["name", "project", "url", "status", "is_active", "interval_seconds", "last_checked_at"]
    list_filter = ["status", "is_active", "monitor_type"]
    search_fields = ["name", "url", "project__name"]
    exclude = ["headers_encrypted"]
    readonly_fields = [
        "status",
        "consecutive_failures",
        "consecutive_successes",
        "consecutive_soft_failures",
        "failing_since",
        "degraded_since",
        "last_checked_at",
        "last_status_change_at",
        "last_response_time_ms",
        "last_status_code",
        "last_error_category",
        "tls_expires_at",
    ]
