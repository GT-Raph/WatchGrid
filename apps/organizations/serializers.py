from rest_framework import serializers

from common.constants import Role

from .models import Invitation, Membership, Organization


class OrganizationSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()
    member_count = serializers.IntegerField(read_only=True)
    project_count = serializers.IntegerField(read_only=True)
    owner_email = serializers.EmailField(source="owner.email", read_only=True)

    class Meta:
        model = Organization
        fields = ["id", "name", "slug", "role", "owner_email", "member_count", "project_count", "created_at"]
        read_only_fields = ["id", "role", "owner_email", "member_count", "project_count", "created_at"]
        extra_kwargs = {"slug": {"required": False}}

    def get_role(self, obj) -> str | None:
        from common.permissions.tenancy import role_for

        request = self.context.get("request")
        return role_for(request.user, obj.id) if request else None

    def validate_name(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("Name is required.")
        return value.strip()


class MembershipSerializer(serializers.ModelSerializer):
    user_id = serializers.UUIDField(source="user.id", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    full_name = serializers.CharField(source="user.full_name", read_only=True)
    is_primary_owner = serializers.SerializerMethodField()

    class Meta:
        model = Membership
        fields = ["id", "user_id", "email", "full_name", "role", "is_primary_owner", "created_at"]
        read_only_fields = ["id", "user_id", "email", "full_name", "is_primary_owner", "created_at"]

    def get_is_primary_owner(self, obj) -> bool:
        return obj.user_id == obj.organization.owner_id


class MembershipRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=Role.choices)


class InvitationSerializer(serializers.ModelSerializer):
    invited_by_email = serializers.EmailField(source="invited_by.email", read_only=True, default=None)
    state = serializers.SerializerMethodField()

    class Meta:
        model = Invitation
        fields = ["id", "email", "role", "state", "invited_by_email", "expires_at", "accepted_at", "created_at"]
        read_only_fields = ["id", "state", "invited_by_email", "expires_at", "accepted_at", "created_at"]

    def get_state(self, obj) -> str:
        if obj.accepted_at:
            return "accepted"
        if obj.revoked_at:
            return "revoked"
        return "pending" if obj.is_pending else "expired"


class InvitationCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=Role.choices, default=Role.VIEWER)


class AcceptInvitationSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=256)
