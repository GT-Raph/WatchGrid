from django.db.models import Count, Q
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.models import AuditLog
from apps.audit.serializers import AuditLogSerializer
from common.permissions.roles import PROJECT_LEVEL_AUDIT_RESOURCES
from common.permissions.tenancy import has_capability, require_capability
from common.permissions.views import TenantScopedMixin, get_scoped_or_404

from . import services
from .models import Invitation, Membership, Organization
from .serializers import (
    AcceptInvitationSerializer,
    InvitationCreateSerializer,
    InvitationSerializer,
    MembershipRoleSerializer,
    MembershipSerializer,
    OrganizationSerializer,
)


class OrganizationViewSet(
    TenantScopedMixin,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = OrganizationSerializer
    queryset = Organization.objects.none()
    organization_lookup = "id"
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    capabilities = {
        "retrieve": "org.view",
        "partial_update": "org.manage",
        "destroy": "org.archive",
    }

    def get_queryset(self):
        return (
            Organization.objects.for_user(self.request.user)
            .select_related("owner")
            .annotate(
                member_count=Count("memberships", distinct=True),
                project_count=Count("projects", filter=Q(projects__status="active"), distinct=True),
            )
            .order_by("name")
        )

    def perform_create(self, serializer):
        data = serializer.validated_data
        serializer.instance = services.create_organization(
            user=self.request.user, name=data["name"], slug=data.get("slug"), request=self.request
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        instance = self.get_queryset().get(pk=serializer.instance.pk)
        return Response(self.get_serializer(instance).data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        services.update_organization(
            user=self.request.user,
            organization=serializer.instance,
            data=serializer.validated_data,
            request=self.request,
        )

    def perform_destroy(self, instance):
        services.archive_organization(user=self.request.user, organization=instance, request=self.request)

    # -- members -------------------------------------------------------------------------

    def _organization(self, pk, capability: str) -> Organization:
        organization = get_scoped_or_404(Organization.objects.for_user(self.request.user), pk)
        require_capability(self.request.user, organization.id, capability)
        return organization

    @extend_schema(responses={200: MembershipSerializer(many=True)})
    @action(detail=True, methods=["get"], url_path="members")
    def members(self, request, pk=None):
        organization = self._organization(pk, "members.view")
        memberships = organization.memberships.select_related("user", "organization").order_by("created_at")
        page = self.paginate_queryset(memberships)
        return self.get_paginated_response(MembershipSerializer(page, many=True).data)

    @extend_schema(request=MembershipRoleSerializer, responses={200: MembershipSerializer})
    @action(detail=True, methods=["patch", "delete"], url_path=r"members/(?P<membership_id>[^/.]+)")
    def member_detail(self, request, pk=None, membership_id=None):
        organization = self._organization(pk, "members.view")
        membership = get_scoped_or_404(
            Membership.objects.select_related("user", "organization").filter(organization=organization),
            membership_id,
        )
        if request.method == "DELETE":
            services.remove_member(actor=request.user, membership=membership, request=request)
            return Response(status=status.HTTP_204_NO_CONTENT)
        serializer = MembershipRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = services.change_member_role(
            actor=request.user, membership=membership, role=serializer.validated_data["role"], request=request
        )
        return Response(MembershipSerializer(membership).data)

    # -- invitations ---------------------------------------------------------------------

    @extend_schema(request=InvitationCreateSerializer, responses={200: InvitationSerializer(many=True)})
    @action(detail=True, methods=["get", "post"], url_path="invitations")
    def invitations(self, request, pk=None):
        organization = self._organization(pk, "members.manage")
        if request.method == "GET":
            invitations = organization.invitations.select_related("invited_by").filter(accepted_at__isnull=True)
            page = self.paginate_queryset(invitations)
            return self.get_paginated_response(InvitationSerializer(page, many=True).data)
        serializer = InvitationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invitation, accept_url = services.invite_member(
            actor=request.user, organization=organization, request=request, **serializer.validated_data
        )
        body = InvitationSerializer(invitation).data
        body["accept_url"] = accept_url  # shown once to the inviter, like an API key secret
        return Response(body, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["delete"], url_path=r"invitations/(?P<invitation_id>[^/.]+)")
    def invitation_detail(self, request, pk=None, invitation_id=None):
        organization = self._organization(pk, "members.manage")
        invitation = get_scoped_or_404(Invitation.objects.filter(organization=organization), invitation_id)
        services.revoke_invitation(actor=request.user, invitation=invitation, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)

    # -- audit ---------------------------------------------------------------------------

    @extend_schema(responses={200: AuditLogSerializer(many=True)})
    @action(detail=True, methods=["get"], url_path="audit-logs")
    def audit_logs(self, request, pk=None):
        organization = self._organization(pk, "audit.view")
        logs = AuditLog.objects.filter(organization=organization)
        if not has_capability(request.user, organization.id, "audit.view_all"):
            logs = logs.filter(resource_type__in=PROJECT_LEVEL_AUDIT_RESOURCES)
        if action_filter := request.query_params.get("action"):
            logs = logs.filter(action__startswith=action_filter)
        if resource_type := request.query_params.get("resource_type"):
            logs = logs.filter(resource_type=resource_type)
        page = self.paginate_queryset(logs)
        return self.get_paginated_response(AuditLogSerializer(page, many=True).data)


class AcceptInvitationView(APIView):
    @extend_schema(request=AcceptInvitationSerializer, responses={200: OrganizationSerializer})
    def post(self, request):
        serializer = AcceptInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = services.accept_invitation(
            user=request.user, token=serializer.validated_data["token"], request=request
        )
        organization = (
            Organization.objects.for_user(request.user)
            .annotate(
                member_count=Count("memberships", distinct=True),
                project_count=Count("projects", filter=Q(projects__status="active"), distinct=True),
            )
            .get(pk=membership.organization_id)
        )
        return Response(OrganizationSerializer(organization, context={"request": request}).data)
