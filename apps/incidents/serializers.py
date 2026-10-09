from rest_framework import serializers

from apps.notifications.models import Notification

from .models import Incident, IncidentEvent


class IncidentListSerializer(serializers.ModelSerializer):
    project = serializers.UUIDField(source="project_id", read_only=True)
    project_name = serializers.CharField(source="project.name", read_only=True)
    monitor = serializers.UUIDField(source="monitor_id", read_only=True)
    monitor_name = serializers.CharField(source="monitor.name", read_only=True, default=None)
    duration_seconds = serializers.IntegerField(read_only=True)
    started_at = serializers.DateTimeField(source="detected_at", read_only=True)

    class Meta:
        model = Incident
        fields = [
            "id",
            "title",
            "incident_key",
            "severity",
            "status",
            "failure_type",
            "summary",
            "project",
            "project_name",
            "monitor",
            "monitor_name",
            "started_at",
            "detected_at",
            "confirmed_at",
            "acknowledged_at",
            "resolved_at",
            "resolution_type",
            "last_seen_at",
            "failure_count",
            "duration_seconds",
        ]
        read_only_fields = fields


class IncidentEventSerializer(serializers.ModelSerializer):
    actor_email = serializers.EmailField(source="actor.email", read_only=True, default=None)

    class Meta:
        model = IncidentEvent
        fields = ["id", "kind", "message", "from_status", "to_status", "actor_email", "data", "created_at"]
        read_only_fields = fields


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = [
            "id",
            "event_type",
            "transition",
            "escalation_level",
            "channel",
            "recipient",
            "status",
            "status_reason",
            "attempts",
            "last_error",
            "sent_at",
            "created_at",
        ]
        read_only_fields = fields


class IncidentDetailSerializer(IncidentListSerializer):
    acknowledged_by_email = serializers.EmailField(source="acknowledged_by.email", read_only=True, default=None)
    resolved_by_email = serializers.EmailField(source="resolved_by.email", read_only=True, default=None)
    monitor_url = serializers.CharField(source="monitor.url", read_only=True, default=None)
    monitor_status = serializers.CharField(source="monitor.status", read_only=True, default=None)
    events = IncidentEventSerializer(many=True, read_only=True)
    notifications = serializers.SerializerMethodField()

    class Meta(IncidentListSerializer.Meta):
        fields = [
            *IncidentListSerializer.Meta.fields,
            "acknowledged_by_email",
            "resolved_by_email",
            "monitor_url",
            "monitor_status",
            "evidence",
            "events",
            "notifications",
        ]
        read_only_fields = fields

    def get_notifications(self, obj) -> list:
        rows = obj.notifications.order_by("-created_at")[:50]
        return NotificationSerializer(rows, many=True).data


class IncidentActionSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, max_length=5000, default="")


class IncidentStatusSerializer(IncidentActionSerializer):
    status = serializers.ChoiceField(choices=Incident.Status.choices)


class IncidentNoteSerializer(serializers.Serializer):
    message = serializers.CharField(max_length=5000)
