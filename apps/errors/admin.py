from django.contrib import admin

from .models import ErrorEvent, ErrorGroup


@admin.register(ErrorGroup)
class ErrorGroupAdmin(admin.ModelAdmin):
    list_display = ["error_type", "project", "environment", "status", "occurrence_count", "affected_users", "last_seen"]
    list_filter = ["status", "environment", "level"]
    search_fields = ["error_type", "normalized_message", "fingerprint"]


@admin.register(ErrorEvent)
class ErrorEventAdmin(admin.ModelAdmin):
    list_display = ["occurred_at", "error_group", "level", "endpoint"]
    list_select_related = ["error_group"]
    show_full_result_count = False

    def has_change_permission(self, request, obj=None):
        return False
