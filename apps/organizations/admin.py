from django.contrib import admin

from .models import Invitation, Membership, Organization


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0
    autocomplete_fields = ["user"]


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "owner", "archived_at", "created_at"]
    search_fields = ["name", "slug", "owner__email"]
    list_filter = ["archived_at"]
    inlines = [MembershipInline]
    autocomplete_fields = ["owner"]


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ["email", "organization", "role", "expires_at", "accepted_at", "revoked_at"]
    search_fields = ["email", "organization__name"]
    exclude = ["token_hash"]
