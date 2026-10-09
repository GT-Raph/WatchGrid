from django.contrib import admin

from .models import Incident, IncidentEvent


class IncidentEventInline(admin.TabularInline):
    model = IncidentEvent
    extra = 0
    readonly_fields = ["kind", "message", "from_status", "to_status", "actor", "created_at"]


@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    list_display = ["title", "project", "severity", "status", "detected_at", "resolved_at"]
    list_filter = ["status", "severity", "incident_key"]
    search_fields = ["title", "project__name", "monitor__name"]
    date_hierarchy = "detected_at"
    inlines = [IncidentEventInline]
