from rest_framework import serializers

from common.constants import Role, Severity
from common.security.urls import UnsafeURLError, validate_url_syntax

from .models import AlertPolicy, EventType, MaintenanceWindow
from .services import public_channels

MAX_CHANNELS = 10
MAX_EMAIL_RECIPIENTS = 50


class ChannelSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=["email", "webhook"])
    to = serializers.ListField(child=serializers.EmailField(), required=False, max_length=MAX_EMAIL_RECIPIENTS)
    to_roles = serializers.ListField(child=serializers.ChoiceField(choices=Role.choices), required=False)
    url = serializers.CharField(required=False, allow_blank=True, max_length=2048, write_only=True)
    url_display = serializers.CharField(required=False, read_only=True)

    def validate(self, attrs):
        if attrs["type"] == "email" and not (attrs.get("to") or attrs.get("to_roles")):
            raise serializers.ValidationError("An email channel needs addresses or member roles.")
        if attrs["type"] == "webhook" and attrs.get("url"):
            try:
                validate_url_syntax(attrs["url"])
            except UnsafeURLError as exc:
                raise serializers.ValidationError({"url": [str(exc)]}) from exc
            if not attrs["url"].lower().startswith("https://"):
                raise serializers.ValidationError({"url": ["Webhook URLs must use HTTPS."]})
        return attrs


class AlertPolicySerializer(serializers.ModelSerializer):
    project = serializers.UUIDField(source="project_id")
    monitor = serializers.UUIDField(source="monitor_id", required=False, allow_null=True)
    event_types = serializers.ListField(child=serializers.ChoiceField(choices=EventType.choices), allow_empty=False)
    min_severity = serializers.ChoiceField(choices=Severity.choices, default=Severity.INFO)
    channels = ChannelSerializer(many=True)
    delay_seconds = serializers.IntegerField(min_value=0, max_value=3600, default=0)
    repeat_interval_minutes = serializers.IntegerField(min_value=0, max_value=1440, default=0)

    class Meta:
        model = AlertPolicy
        fields = [
            "id",
            "project",
            "name",
            "enabled",
            "monitor",
            "event_types",
            "min_severity",
            "delay_seconds",
            "repeat_interval_minutes",
            "channels",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_channels(self, value):
        if not value:
            raise serializers.ValidationError("At least one channel is required.")
        if len(value) > MAX_CHANNELS:
            raise serializers.ValidationError(f"At most {MAX_CHANNELS} channels are allowed.")
        return value

    def validate_repeat_interval_minutes(self, value: int) -> int:
        if value and value < 5:
            raise serializers.ValidationError("Reminders cannot repeat more often than every 5 minutes.")
        return value

    def validate_event_types(self, value):
        return sorted(set(value))

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["channels"] = public_channels(instance.channels)
        return data


class AlertPolicyUpdateSerializer(AlertPolicySerializer):
    project = serializers.UUIDField(source="project_id", read_only=True)


class MaintenanceWindowSerializer(serializers.ModelSerializer):
    project = serializers.UUIDField(source="project_id")
    monitor = serializers.UUIDField(source="monitor_id", required=False, allow_null=True)
    is_active = serializers.SerializerMethodField()

    class Meta:
        model = MaintenanceWindow
        fields = ["id", "project", "monitor", "title", "starts_at", "ends_at", "is_active", "created_at"]
        read_only_fields = ["id", "is_active", "created_at"]

    def get_is_active(self, obj) -> bool:
        from django.utils import timezone

        return obj.starts_at <= timezone.now() < obj.ends_at

    def validate(self, attrs):
        starts = attrs.get("starts_at", getattr(self.instance, "starts_at", None))
        ends = attrs.get("ends_at", getattr(self.instance, "ends_at", None))
        if starts and ends and ends <= starts:
            raise serializers.ValidationError({"ends_at": ["The window must end after it starts."]})
        if starts and ends and (ends - starts).days > 31:
            raise serializers.ValidationError({"ends_at": ["Maintenance windows are limited to 31 days."]})
        return attrs


class MaintenanceWindowUpdateSerializer(MaintenanceWindowSerializer):
    project = serializers.UUIDField(source="project_id", read_only=True)
