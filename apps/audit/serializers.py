from rest_framework import serializers

from .models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = [
            "id",
            "action",
            "actor_email",
            "resource_type",
            "resource_id",
            "metadata",
            "ip_address",
            "request_id",
            "created_at",
        ]
        read_only_fields = fields
