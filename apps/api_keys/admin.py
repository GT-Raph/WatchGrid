from django.contrib import admin

from .models import APIKey


@admin.register(APIKey)
class APIKeyAdmin(admin.ModelAdmin):
    list_display = ["name", "project", "key_prefix", "last_used_at", "revoked_at", "created_at"]
    search_fields = ["name", "key_prefix", "project__name"]
    readonly_fields = ["key_prefix", "key_hash", "last_used_at"]
