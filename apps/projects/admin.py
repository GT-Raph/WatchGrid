from django.contrib import admin

from .models import Project


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ["name", "organization", "environment", "status", "created_at"]
    list_filter = ["status", "environment"]
    search_fields = ["name", "slug", "organization__name"]
