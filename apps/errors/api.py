import logging

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from drf_spectacular.utils import OpenApiParameter, extend_schema
from kombu.exceptions import OperationalError as BrokerError
from rest_framework import mixins, status, viewsets
from rest_framework.authentication import BaseAuthentication
from rest_framework.decorators import action
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from apps.api_keys.models import APIKey
from apps.api_keys.services import authenticate_key
from common.exceptions import DomainError, ServiceUnavailable
from common.permissions.views import TenantScopedMixin

from . import services
from .models import ErrorGroup
from .serializers import (
    ErrorEventIngestSerializer,
    ErrorEventSerializer,
    ErrorGroupDetailSerializer,
    ErrorGroupListSerializer,
    ErrorGroupStatusSerializer,
)

logger = logging.getLogger("watchgrid.errors")


class PayloadTooLarge(DomainError):
    code = "PAYLOAD_TOO_LARGE"
    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    default_message = "The event payload exceeds the maximum allowed size."


class ProjectKeyAuthentication(BaseAuthentication):
    """Authenticate ingestion with a project key (header preferred, body accepted)."""

    keyword = "Bearer"

    def authenticate(self, request):
        raw = request.META.get("HTTP_X_PROJECT_KEY", "")
        auth = request.META.get("HTTP_AUTHORIZATION", "")
        if not raw and auth.startswith(f"{self.keyword} "):
            raw = auth[len(self.keyword) + 1 :]
        if not raw and isinstance(request.data, dict):
            raw = str(request.data.get("project_key", ""))
        if not raw:
            raise AuthenticationFailed("A project key is required.")
        api_key = authenticate_key(raw, scope=APIKey.Scope.EVENTS_WRITE)
        if api_key is None:
            raise AuthenticationFailed("The project key is invalid or revoked.")
        return AnonymousUser(), api_key

    def authenticate_header(self, request):
        return self.keyword


class HasProjectKey(BasePermission):
    def has_permission(self, request, view) -> bool:
        return isinstance(request.auth, APIKey)


class IngestionThrottle(SimpleRateThrottle):
    """Per-project-key rate limit (Section 16.4)."""

    scope = "ingestion"

    def get_cache_key(self, request, view):
        if isinstance(request.auth, APIKey):
            return self.cache_format % {"scope": self.scope, "ident": str(request.auth.project_id)}
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class ErrorIngestView(APIView):
    authentication_classes = [ProjectKeyAuthentication]
    permission_classes = [HasProjectKey]
    throttle_classes = [IngestionThrottle]

    def initial(self, request, *args, **kwargs):
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        if length > settings.ERROR_EVENT_MAX_BYTES:
            raise PayloadTooLarge()
        super().initial(request, *args, **kwargs)

    @extend_schema(request=ErrorEventIngestSerializer, responses={202: dict}, auth=[])
    def post(self, request):
        serializer = ErrorEventIngestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        project = request.auth.project
        payload = services.prepare_event(project=project, data=serializer.validated_data)
        try:
            services.enqueue_event(payload)
        except BrokerError as exc:
            logger.error("ingestion_enqueue_failed", extra={"project_id": str(project.id)})
            raise ServiceUnavailable("Event ingestion is temporarily unavailable. Retry with backoff.") from exc
        return Response(
            {"id": payload["event_id"], "fingerprint": payload["fingerprint"], "sampled": not payload["store_raw"]},
            status=status.HTTP_202_ACCEPTED,
        )


class ErrorGroupViewSet(
    TenantScopedMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = ErrorGroup.objects.none()
    http_method_names = ["get", "post", "patch", "head", "options"]
    capabilities = {
        "retrieve": "errors.view",
        "partial_update": "errors.manage",
        "resolve": "errors.manage",
        "events": "errors.view",
    }

    def get_queryset(self):
        qs = ErrorGroup.objects.for_user(self.request.user).select_related(
            "project", "project__organization", "resolved_by"
        )
        if self.action == "list":
            params = self.request.query_params
            if project := params.get("project"):
                qs = qs.filter(project_id=project)
            if organization := params.get("organization"):
                qs = qs.filter(project__organization_id=organization)
            if environment := params.get("environment"):
                qs = qs.filter(environment=environment)
            status_filter = params.get("status", "unresolved")
            if status_filter != "all":
                qs = qs.filter(status=status_filter)
            qs = qs.order_by("-last_seen")
        return qs

    def get_serializer_class(self):
        return ErrorGroupDetailSerializer if self.action == "retrieve" else ErrorGroupListSerializer

    @extend_schema(request=ErrorGroupStatusSerializer, responses={200: ErrorGroupDetailSerializer})
    def partial_update(self, request, *args, **kwargs):
        group = self.get_object()
        serializer = ErrorGroupStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.set_status(
            actor=request.user, group=group, status=serializer.validated_data["status"], request=request
        )
        return Response(ErrorGroupDetailSerializer(group).data)

    @extend_schema(request=None, responses={200: ErrorGroupDetailSerializer})
    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        group = self.get_object()
        services.set_status(actor=request.user, group=group, status=ErrorGroup.Status.RESOLVED, request=request)
        return Response(ErrorGroupDetailSerializer(group).data)

    @extend_schema(
        parameters=[OpenApiParameter("user", str, description="Filter by pseudonymous user hash")],
        responses={200: ErrorEventSerializer(many=True)},
    )
    @action(detail=True, methods=["get"])
    def events(self, request, pk=None):
        group = self.get_object()
        events = group.events.order_by("-occurred_at")
        if user_hash := request.query_params.get("user"):
            events = events.filter(user_reference_hash=user_hash)
        page = self.paginate_queryset(events)
        return self.get_paginated_response(ErrorEventSerializer(page, many=True).data)
