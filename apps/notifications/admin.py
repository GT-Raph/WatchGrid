from django.contrib import admin

from .models import AlertPolicy, MaintenanceWindow, Notification


@admin.register(AlertPolicy)
class AlertPolicyAdmin(admin.ModelAdmin):
    list_display = ["name", "project", "enabled", "min_severity", "delay_seconds", "repeat_interval_minutes"]
    list_filter = ["enabled", "min_severity"]
    search_fields = ["name", "project__name"]


@admin.register(MaintenanceWindow)
class MaintenanceWindowAdmin(admin.ModelAdmin):
    list_display = ["title", "project", "monitor", "starts_at", "ends_at"]


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["created_at", "event_type", "channel", "recipient", "status", "attempts", "sent_at"]
    list_filter = ["status", "channel", "event_type"]
    search_fields = ["recipient", "dedupe_key"]
    exclude = ["target_encrypted"]
