from django.db.models import Count, Q
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.api_keys.serializers import APIKeyCreateSerializer, APIKeySerializer
from apps.api_keys.services import create_api_key
from apps.errors.models import ErrorGroup
from apps.errors.serializers import ErrorGroupListSerializer
from apps.organizations.models import Organization
from common.permissions.tenancy import require_capability
from common.permissions.views import TenantScopedMixin, get_scoped_or_404

from . import services
from .models import Project
from .serializers import ProjectSerializer, ProjectUpdateSerializer


class ProjectViewSet(
    TenantScopedMixin,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = ProjectSerializer
    queryset = Project.objects.none()
    organization_lookup = "organization_id"
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    capabilities = {
        "retrieve": "project.view",
        "partial_update": "project.edit",
        "destroy": "project.archive",
        "restore": "project.archive",
        "errors": "errors.view",
    }

    def get_queryset(self):
        qs = (
            Project.objects.for_user(self.request.user)
            .select_related("organization")
            .annotate(
                monitor_count=Count("monitors", filter=Q(monitors__archived_at__isnull=True), distinct=True),
                monitors_down=Count(
                    "monitors",
                    filter=Q(monitors__archived_at__isnull=True, monitors__status="down"),
                    distinct=True,
                ),
                monitors_degraded=Count(
                    "monitors",
                    filter=Q(monitors__archived_at__isnull=True, monitors__status="degraded"),
                    distinct=True,
                ),
                active_incidents=Count("incidents", filter=~Q(incidents__status="resolved"), distinct=True),
            )
            .order_by("organization__name", "name")
        )
        if self.action == "list":
            params = self.request.query_params
            if organization := params.get("organization"):
                qs = qs.filter(organization_id=organization)
            status_filter = params.get("status", "active")
            if status_filter != "all":
                qs = qs.filter(status=status_filter)
            if search := params.get("search"):
                qs = qs.filter(name__icontains=search)
        return qs

    def get_serializer_class(self):
        if self.action == "partial_update":
            return ProjectUpdateSerializer
        return ProjectSerializer

    def _detail(self, project_id):
        return ProjectSerializer(self.get_queryset().get(pk=project_id), context={"request": self.request}).data

    def create(self, request, *args, **kwargs):
        serializer = ProjectSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        organization = get_scoped_or_404(Organization.objects.for_user(request.user), data["organization_id"])
        project = services.create_project(actor=request.user, organization=organization, data=data, request=request)
        return Response(self._detail(project.pk), status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        project = self.get_object()
        serializer = ProjectUpdateSerializer(project, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        services.update_project(actor=request.user, project=project, data=serializer.validated_data, request=request)
        return Response(self._detail(project.pk))

    def perform_destroy(self, instance):
        services.archive_project(actor=self.request.user, project=instance, request=self.request)

    @extend_schema(request=None, responses={200: ProjectSerializer})
    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        project = self.get_object()
        services.restore_project(actor=request.user, project=project, request=request)
        return Response(self._detail(project.pk))

    @extend_schema(responses={200: ErrorGroupListSerializer(many=True)})
    @action(detail=True, methods=["get"])
    def errors(self, request, pk=None):
        project = self.get_object()
        groups = ErrorGroup.objects.filter(project=project)
        params = request.query_params
        if environment := params.get("environment"):
            groups = groups.filter(environment=environment)
        status_filter = params.get("status", "unresolved")
        if status_filter != "all":
            groups = groups.filter(status=status_filter)
        if search := params.get("search"):
            groups = groups.filter(Q(error_type__icontains=search) | Q(normalized_message__icontains=search))
        ordering = {"last_seen": "-last_seen", "count": "-occurrence_count", "users": "-affected_users"}
        groups = groups.order_by(ordering.get(params.get("sort", "last_seen"), "-last_seen"))
        page = self.paginate_queryset(groups)
        return self.get_paginated_response(ErrorGroupListSerializer(page, many=True).data)

    @extend_schema(request=APIKeyCreateSerializer, responses={200: APIKeySerializer(many=True)})
    @action(detail=True, methods=["get", "post"], url_path="api-keys")
    def api_keys(self, request, pk=None):
        project = self.get_object()
        if request.method == "GET":
            require_capability(request.user, project.organization_id, "apikey.view")
            keys = project.api_keys.select_related("created_by").order_by("-created_at")
            page = self.paginate_queryset(keys)
            return self.get_paginated_response(APIKeySerializer(page, many=True).data)
        serializer = APIKeyCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        api_key, raw_key = create_api_key(
            actor=request.user, project=project, name=serializer.validated_data["name"], request=request
        )
        body = APIKeySerializer(api_key).data
        body["key"] = raw_key  # displayed exactly once
        return Response(body, status=status.HTTP_201_CREATED)
