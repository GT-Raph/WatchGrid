from django.conf import settings
from rest_framework import serializers

from common.security.redaction import REDACTED, is_sensitive_header
from common.security.urls import UnsafeURLError, validate_url_syntax

from .models import Monitor
from .validators import ExpectedStatusError, parse_expected_status

BODY_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
MAX_HEADERS = 30


class HeadersField(serializers.DictField):
    """Request headers: write accepts plain values; read returns sensitive values redacted."""

    child = serializers.CharField(max_length=4096, allow_blank=True)

    def to_representation(self, value):
        return {k: (REDACTED if is_sensitive_header(k) else v) for k, v in (value or {}).items()}


class MonitorSerializer(serializers.ModelSerializer):
    project = serializers.UUIDField(source="project_id")
    project_name = serializers.CharField(source="project.name", read_only=True)
    organization = serializers.UUIDField(source="project.organization_id", read_only=True)
    headers = HeadersField(required=False)
    uptime_24h = serializers.SerializerMethodField()
    recent_checks = serializers.SerializerMethodField()

    class Meta:
        model = Monitor
        fields = [
            "id",
            "project",
            "project_name",
            "organization",
            "name",
            "monitor_type",
            "url",
            "http_method",
            "interval_seconds",
            "timeout_seconds",
            "expected_status",
            "response_time_threshold_ms",
            "failure_threshold",
            "recovery_threshold",
            "follow_redirects",
            "max_redirects",
            "headers",
            "body",
            "assertion_mode",
            "assertion_text",
            # runtime state
            "status",
            "is_active",
            "next_check_at",
            "last_checked_at",
            "last_status_change_at",
            "last_response_time_ms",
            "last_status_code",
            "last_error_category",
            "consecutive_failures",
            "consecutive_successes",
            "tls_expires_at",
            "archived_at",
            "created_at",
            "updated_at",
            "uptime_24h",
            "recent_checks",
        ]
        read_only_fields = [
            "id",
            "project_name",
            "organization",
            "status",
            "is_active",
            "next_check_at",
            "last_checked_at",
            "last_status_change_at",
            "last_response_time_ms",
            "last_status_code",
            "last_error_category",
            "consecutive_failures",
            "consecutive_successes",
            "tls_expires_at",
            "archived_at",
            "created_at",
            "updated_at",
            "uptime_24h",
            "recent_checks",
        ]

    def get_uptime_24h(self, obj) -> float | None:
        return (self.context.get("uptime") or {}).get(obj.id)

    def get_recent_checks(self, obj) -> list:
        return (self.context.get("recent") or {}).get(obj.id, [])

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["headers"] = HeadersField().to_representation(instance.headers)
        if "uptime" not in self.context:
            data.pop("uptime_24h", None)
            data.pop("recent_checks", None)
        return data

    # -- field validation ------------------------------------------------------------------

    def validate_name(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("Name is required.")
        return value.strip()

    def validate_url(self, value: str) -> str:
        try:
            validate_url_syntax(value)
        except UnsafeURLError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return value

    def validate_interval_seconds(self, value: int) -> int:
        if value < settings.MONITOR_MIN_INTERVAL_SECONDS:
            raise serializers.ValidationError(f"Minimum interval is {settings.MONITOR_MIN_INTERVAL_SECONDS} seconds.")
        if value > settings.MONITOR_MAX_INTERVAL_SECONDS:
            raise serializers.ValidationError(f"Maximum interval is {settings.MONITOR_MAX_INTERVAL_SECONDS} seconds.")
        return value

    def validate_timeout_seconds(self, value: int) -> int:
        if not 1 <= value <= settings.MONITOR_MAX_TIMEOUT_SECONDS:
            raise serializers.ValidationError(
                f"Timeout must be between 1 and {settings.MONITOR_MAX_TIMEOUT_SECONDS} seconds."
            )
        return value

    def validate_expected_status(self, value: str) -> str:
        try:
            parse_expected_status(value)
        except ExpectedStatusError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return value.replace(" ", "")

    def validate_max_redirects(self, value: int) -> int:
        return min(value, settings.MONITOR_MAX_REDIRECTS)

    def validate_headers(self, value: dict) -> dict:
        if len(value) > MAX_HEADERS:
            raise serializers.ValidationError(f"At most {MAX_HEADERS} headers are allowed.")
        for key in value:
            if not key or any(ch in key for ch in ":\r\n ") or len(key) > 128:
                raise serializers.ValidationError(f"Invalid header name '{key}'.")
            if key.lower() in {"host", "content-length", "transfer-encoding", "connection"}:
                raise serializers.ValidationError(f"Header '{key}' is managed by the platform.")
        return value

    def validate_body(self, value: str) -> str:
        if len(value.encode()) > settings.MONITOR_MAX_REQUEST_BODY_BYTES:
            raise serializers.ValidationError(
                f"Request body is limited to {settings.MONITOR_MAX_REQUEST_BODY_BYTES} bytes."
            )
        return value

    def validate(self, attrs):
        instance = self.instance

        def current(field, default=None):
            if field in attrs:
                return attrs[field]
            return getattr(instance, field) if instance is not None else default

        interval = current("interval_seconds", 60)
        timeout = current("timeout_seconds", 10)
        if timeout >= interval:
            raise serializers.ValidationError({"timeout_seconds": ["Timeout must be shorter than the interval."]})
        method = current("http_method", "GET")
        if current("body", "") and method not in BODY_METHODS:
            raise serializers.ValidationError({"body": [f"{method} requests cannot carry a body."]})
        mode = current("assertion_mode", Monitor.AssertionMode.NONE)
        if mode != Monitor.AssertionMode.NONE and not current("assertion_text", ""):
            raise serializers.ValidationError({"assertion_text": ["Provide the text to look for."]})
        if mode != Monitor.AssertionMode.NONE and method == "HEAD":
            raise serializers.ValidationError({"assertion_mode": ["HEAD responses have no body to inspect."]})
        return attrs


class MonitorUpdateSerializer(MonitorSerializer):
    project = serializers.UUIDField(source="project_id", read_only=True)


class MonitorCheckSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kind = serializers.CharField()
    checked_at = serializers.DateTimeField()
    scheduled_for = serializers.DateTimeField(allow_null=True)
    outcome = serializers.CharField()
    success = serializers.BooleanField()
    status_code = serializers.IntegerField(allow_null=True)
    response_time_ms = serializers.IntegerField(allow_null=True)
    error_category = serializers.CharField()
    error_message = serializers.CharField()
    timeout_phase = serializers.CharField()
    resolved_ip = serializers.CharField(allow_null=True)
    redirect_count = serializers.IntegerField()
    response_size_bytes = serializers.IntegerField(allow_null=True)
    tls_expires_at = serializers.DateTimeField(allow_null=True)
    tls_issuer = serializers.CharField()
    monitor_status_after = serializers.CharField()
