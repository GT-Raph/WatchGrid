import json

from django.conf import settings
from rest_framework import serializers

from .models import ErrorEvent, ErrorGroup

LEVELS = ["fatal", "critical", "error", "warning", "info", "debug"]
MAX_METADATA_BYTES = 16 * 1024


class ErrorEventIngestSerializer(serializers.Serializer):
    """Payload accepted by ``POST /api/v1/events/errors/`` (Section 16.1)."""

    event_id = serializers.UUIDField(required=False, help_text="Client-generated id for idempotent retries.")
    project_key = serializers.CharField(required=False, write_only=True, max_length=128)
    environment = serializers.RegexField(r"^[A-Za-z0-9_.-]{1,64}$", required=False, default="production")
    level = serializers.ChoiceField(choices=LEVELS, required=False, default="error")
    error_type = serializers.CharField(max_length=200)
    message = serializers.CharField(max_length=5000, trim_whitespace=False)
    endpoint = serializers.CharField(max_length=500, required=False, allow_blank=True)
    http_method = serializers.CharField(max_length=10, required=False, allow_blank=True)
    application_version = serializers.CharField(max_length=100, required=False, allow_blank=True)
    user_reference = serializers.CharField(max_length=256, required=False, allow_blank=True)
    stack_trace = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    metadata = serializers.DictField(required=False)
    occurred_at = serializers.DateTimeField(required=False)
    fingerprint = serializers.ListField(
        child=serializers.CharField(max_length=200), required=False, max_length=10, allow_empty=False
    )

    def validate_stack_trace(self, value: str) -> str:
        return value[: settings.ERROR_EVENT_MAX_STACK_TRACE_CHARS]

    def validate_metadata(self, value: dict) -> dict:
        if len(json.dumps(value, default=str)) > MAX_METADATA_BYTES:
            raise serializers.ValidationError(f"Metadata is limited to {MAX_METADATA_BYTES} bytes.")
        return value


class ErrorGroupListSerializer(serializers.ModelSerializer):
    project = serializers.UUIDField(source="project_id", read_only=True)

    class Meta:
        model = ErrorGroup
        fields = [
            "id",
            "project",
            "environment",
            "level",
            "error_type",
            "normalized_message",
            "endpoint",
            "http_method",
            "status",
            "occurrence_count",
            "affected_users",
            "first_seen",
            "last_seen",
            "last_release",
            "regression_count",
        ]
        read_only_fields = fields


class ErrorEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ErrorEvent
        fields = [
            "id",
            "occurred_at",
            "received_at",
            "level",
            "message",
            "endpoint",
            "http_method",
            "application_version",
            "user_reference_hash",
            "stack_trace",
            "metadata",
        ]
        read_only_fields = fields


class ErrorGroupDetailSerializer(ErrorGroupListSerializer):
    project_name = serializers.CharField(source="project.name", read_only=True)
    resolved_by_email = serializers.EmailField(source="resolved_by.email", read_only=True, default=None)
    latest_event = serializers.SerializerMethodField()

    class Meta(ErrorGroupListSerializer.Meta):
        fields = [
            *ErrorGroupListSerializer.Meta.fields,
            "project_name",
            "sample_message",
            "top_frame",
            "fingerprint",
            "fingerprint_version",
            "stored_event_count",
            "resolved_at",
            "resolved_by_email",
            "latest_event",
        ]
        read_only_fields = fields

    def get_latest_event(self, obj) -> dict | None:
        event = obj.events.order_by("-occurred_at").first()
        return ErrorEventSerializer(event).data if event else None


class ErrorGroupStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=ErrorGroup.Status.choices)
