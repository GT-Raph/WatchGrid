from django.db.models import Q
from django.utils.dateparse import parse_datetime
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from common.permissions.views import TenantScopedMixin

from . import services
from .models import Incident
from .serializers import (
    IncidentActionSerializer,
    IncidentDetailSerializer,
    IncidentEventSerializer,
    IncidentListSerializer,
    IncidentNoteSerializer,
    IncidentStatusSerializer,
)


class IncidentViewSet(
    TenantScopedMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Incident.objects.none()
    http_method_names = ["get", "post", "patch", "head", "options"]
    capabilities = {
        "retrieve": "incident.view",
        "partial_update": "incident.manage",
        "acknowledge": "incident.manage",
        "resolve": "incident.manage",
        "notes": "incident.manage",
    }

    def get_queryset(self):
        qs = Incident.objects.for_user(self.request.user).select_related(
            "project", "project__organization", "monitor", "acknowledged_by", "resolved_by"
        )
        if self.action == "retrieve":
            return qs.prefetch_related("events__actor")
        if self.action != "list":
            return qs
        params = self.request.query_params
        status_filter = params.get("status")
        if status_filter == "active":
            qs = qs.active()
        elif status_filter:
            qs = qs.filter(status__in=status_filter.split(","))
        if severity := params.get("severity"):
            qs = qs.filter(severity__in=severity.split(","))
        if project := params.get("project"):
            qs = qs.filter(project_id=project)
        if organization := params.get("organization"):
            qs = qs.filter(project__organization_id=organization)
        if monitor := params.get("monitor"):
            qs = qs.filter(monitor_id=monitor)
        if since := parse_datetime(params.get("since", "") or ""):
            qs = qs.filter(Q(detected_at__gte=since) | ~Q(status=Incident.Status.RESOLVED))
        if search := params.get("search"):
            qs = qs.filter(Q(title__icontains=search) | Q(summary__icontains=search))
        return qs.order_by("-detected_at")

    def get_serializer_class(self):
        return IncidentDetailSerializer if self.action == "retrieve" else IncidentListSerializer

    def _detail(self, incident):
        fresh = self.get_queryset().prefetch_related("events__actor").get(pk=incident.pk)
        return IncidentDetailSerializer(fresh).data

    @extend_schema(
        parameters=[
            OpenApiParameter("status", str, description="active, resolved or comma-separated statuses"),
            OpenApiParameter("severity", str),
            OpenApiParameter("project", str),
            OpenApiParameter("monitor", str),
        ]
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(request=IncidentStatusSerializer, responses={200: IncidentDetailSerializer})
    def partial_update(self, request, *args, **kwargs):
        incident = self.get_object()
        serializer = IncidentStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.change_status(
            actor=request.user,
            incident=incident,
            status=serializer.validated_data["status"],
            note=serializer.validated_data["note"],
            request=request,
        )
        return Response(self._detail(incident))

    @extend_schema(request=IncidentActionSerializer, responses={200: IncidentDetailSerializer})
    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        incident = self.get_object()
        serializer = IncidentActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.acknowledge(
            actor=request.user, incident=incident, note=serializer.validated_data["note"], request=request
        )
        return Response(self._detail(incident))

    @extend_schema(request=IncidentActionSerializer, responses={200: IncidentDetailSerializer})
    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        incident = self.get_object()
        serializer = IncidentActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.resolve_manually(
            actor=request.user, incident=incident, note=serializer.validated_data["note"], request=request
        )
        return Response(self._detail(incident))

    @extend_schema(request=IncidentNoteSerializer, responses={201: IncidentEventSerializer})
    @action(detail=True, methods=["post"])
    def notes(self, request, pk=None):
        incident = self.get_object()
        serializer = IncidentNoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        event = services.add_note(actor=request.user, incident=incident, message=serializer.validated_data["message"])
        return Response(IncidentEventSerializer(event).data, status=status.HTTP_201_CREATED)
