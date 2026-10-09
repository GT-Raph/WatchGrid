from django.contrib import admin

from .models import MonitorCheck


@admin.register(MonitorCheck)
class MonitorCheckAdmin(admin.ModelAdmin):
    list_display = ["checked_at", "monitor", "kind", "outcome", "status_code", "response_time_ms", "error_category"]
    list_filter = ["outcome", "kind", "error_category"]
    search_fields = ["monitor__name", "execution_key"]
    date_hierarchy = "checked_at"
    list_select_related = ["monitor"]
    show_full_result_count = False

    def has_change_permission(self, request, obj=None):
        return False
