from rest_framework import serializers

from .models import APIKey


class APIKeySerializer(serializers.ModelSerializer):
    project = serializers.UUIDField(source="project_id", read_only=True)
    created_by_email = serializers.EmailField(source="created_by.email", read_only=True, default=None)
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = APIKey
        fields = [
            "id",
            "project",
            "name",
            "key_prefix",
            "scopes",
            "is_active",
            "created_by_email",
            "last_used_at",
            "revoked_at",
            "created_at",
        ]
        read_only_fields = fields


class APIKeyCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120)
