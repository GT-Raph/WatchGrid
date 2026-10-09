from datetime import timedelta

from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from apps.analytics import services as analytics
from apps.analytics.services import STATUS_PRIORITY
from apps.checks.models import MonitorCheck
from apps.projects.models import Project
from common.permissions.views import TenantScopedMixin, get_scoped_or_404
from common.utilities.time import TimeRange, parse_time_range

from . import services
from .models import Monitor
from .serializers import MonitorCheckSerializer, MonitorSerializer, MonitorUpdateSerializer

RANGE_PARAM = OpenApiParameter("range", str, description="1h, 24h, 7d, 30d, 90d or custom (with start/end)")


class MonitorViewSet(
    TenantScopedMixin,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = MonitorSerializer
    queryset = Monitor.objects.none()
    throttle_scope: str | None = None  # set per action (manual checks)
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    capabilities = {
        "retrieve": "monitor.view",
        "partial_update": "monitor.manage",
        "destroy": "monitor.manage",
        "test": "monitor.operate",
        "pause": "monitor.operate",
        "resume": "monitor.operate",
        "checks": "monitor.view",
        "statistics": "monitor.view",
    }

    def get_queryset(self):
        qs = Monitor.objects.for_user(self.request.user).select_related("project", "project__organization")
        if self.action == "list":
            params = self.request.query_params
            if params.get("include_archived") != "true":
                qs = qs.filter(archived_at__isnull=True)
            if project := params.get("project"):
                qs = qs.filter(project_id=project)
            if organization := params.get("organization"):
                qs = qs.filter(project__organization_id=organization)
            if status_filter := params.get("status"):
                qs = qs.filter(status__in=status_filter.split(","))
            if search := params.get("search"):
                qs = qs.filter(Q(name__icontains=search) | Q(url__icontains=search))
            sort = params.get("sort", "name")
            if sort == "status":
                # Most urgent first: down, failing, recovering, degraded, no data, operational, paused.
                priority = Case(
                    *[When(status=status, then=Value(rank)) for status, rank in STATUS_PRIORITY.items()],
                    default=Value(9),
                    output_field=IntegerField(),
                )
                qs = qs.annotate(priority=priority).order_by("priority", "name", "id")
            else:
                ordering = {"name": "name", "recent": "-last_status_change_at"}
                qs = qs.order_by(ordering.get(sort, "name"), "id")
        return qs

    def get_serializer_class(self):
        if self.action == "partial_update":
            return MonitorUpdateSerializer
        return MonitorSerializer

    def list(self, request, *args, **kwargs):
        page = self.paginate_queryset(self.get_queryset())
        ids = [m.id for m in page]
        now = timezone.now()
        try:
            bars = max(1, min(int(request.query_params.get("bars", 30)), 90))
        except ValueError:
            bars = 30
        context = {
            **self.get_serializer_context(),
            "uptime": analytics.uptime_for_monitors(ids, TimeRange(now - timedelta(hours=24), now, "24h")),
            "recent": analytics.recent_outcomes(ids, limit=bars),
        }
        return self.get_paginated_response(MonitorSerializer(page, many=True, context=context).data)

    def create(self, request, *args, **kwargs):
        serializer = MonitorSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        project = get_scoped_or_404(
            Project.objects.for_user(request.user).select_related("organization"), data.pop("project_id")
        )
        monitor = services.create_monitor(actor=request.user, project=project, data=data, request=request)
        return Response(MonitorSerializer(monitor).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        monitor = self.get_object()
        serializer = MonitorUpdateSerializer(monitor, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        monitor = services.update_monitor(
            actor=request.user, monitor=monitor, data=dict(serializer.validated_data), request=request
        )
        return Response(MonitorSerializer(monitor).data)

    def perform_destroy(self, instance):
        services.archive_monitor(actor=self.request.user, monitor=instance, request=self.request)

    @extend_schema(request=None, responses={202: dict})
    @action(detail=True, methods=["post"], throttle_classes=[ScopedRateThrottle], throttle_scope="manual_check")
    def test(self, request, pk=None):
        execution_key = services.request_manual_check(actor=request.user, monitor=self.get_object(), request=request)
        return Response(
            {"execution_key": execution_key, "detail": "Manual check queued. Results appear in check history."},
            status=status.HTTP_202_ACCEPTED,
        )

    @extend_schema(request=None, responses={200: MonitorSerializer})
    @action(detail=True, methods=["post"])
    def pause(self, request, pk=None):
        monitor = services.pause_monitor(actor=request.user, monitor=self.get_object(), request=request)
        return Response(MonitorSerializer(monitor).data)

    @extend_schema(request=None, responses={200: MonitorSerializer})
    @action(detail=True, methods=["post"])
    def resume(self, request, pk=None):
        monitor = services.resume_monitor(actor=request.user, monitor=self.get_object(), request=request)
        return Response(MonitorSerializer(monitor).data)

    @extend_schema(
        parameters=[
            OpenApiParameter("outcome", str, description="success, degraded, failure, platform_error"),
            OpenApiParameter("kind", str, description="scheduled or manual"),
            OpenApiParameter("execution_key", str),
        ],
        responses={200: MonitorCheckSerializer(many=True)},
    )
    @action(detail=True, methods=["get"])
    def checks(self, request, pk=None):
        monitor = self.get_object()
        checks = MonitorCheck.objects.filter(monitor=monitor).order_by("-checked_at", "-id")
        params = request.query_params
        if outcome := params.get("outcome"):
            checks = checks.filter(outcome__in=outcome.split(","))
        if kind := params.get("kind"):
            checks = checks.filter(kind=kind)
        if key := params.get("execution_key"):
            checks = checks.filter(execution_key=key)
        if params.get("range") or params.get("start"):
            tr = parse_time_range(params)
            checks = checks.filter(checked_at__gte=tr.start, checked_at__lt=tr.end)
        page = self.paginate_queryset(checks)
        return self.get_paginated_response(MonitorCheckSerializer(page, many=True).data)

    @extend_schema(parameters=[RANGE_PARAM], responses={200: dict})
    @action(detail=True, methods=["get"])
    def statistics(self, request, pk=None):
        monitor = self.get_object()
        tr = parse_time_range(request.query_params, default="24h")
        payload = analytics.monitor_statistics(monitor, tr)
        payload["series"] = analytics.monitor_series(monitor, tr)
        return Response(payload)
