from django.contrib import admin

from .models import MonitorHourlyStat


@admin.register(MonitorHourlyStat)
class MonitorHourlyStatAdmin(admin.ModelAdmin):
    list_display = ["monitor", "hour", "total_checks", "up_checks", "failed_checks", "response_time_p95"]
    list_select_related = ["monitor"]
