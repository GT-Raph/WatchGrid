from rest_framework import serializers

from common.constants import Environment

from .models import Project


class ProjectSerializer(serializers.ModelSerializer):
    organization = serializers.UUIDField(source="organization_id")
    organization_name = serializers.CharField(source="organization.name", read_only=True)
    monitor_count = serializers.IntegerField(read_only=True, default=0)
    monitors_down = serializers.IntegerField(read_only=True, default=0)
    monitors_degraded = serializers.IntegerField(read_only=True, default=0)
    active_incidents = serializers.IntegerField(read_only=True, default=0)
    environment = serializers.ChoiceField(choices=Environment.choices, default=Environment.PRODUCTION)

    class Meta:
        model = Project
        fields = [
            "id",
            "organization",
            "organization_name",
            "name",
            "slug",
            "description",
            "environment",
            "status",
            "archived_at",
            "monitor_count",
            "monitors_down",
            "monitors_degraded",
            "active_incidents",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "organization_name", "status", "archived_at", "created_at", "updated_at"]
        extra_kwargs = {"slug": {"required": False}}

    def validate_name(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("Name is required.")
        return value.strip()


class ProjectUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = ["name", "description", "environment"]
