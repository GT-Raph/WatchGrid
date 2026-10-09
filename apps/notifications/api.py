from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.audit.services import record_audit
from apps.monitors.models import Monitor
from apps.projects.models import Project
from common.permissions.tenancy import require_capability
from common.permissions.views import TenantScopedMixin, get_scoped_or_404

from . import services
from .models import AlertPolicy, MaintenanceWindow
from .serializers import (
    AlertPolicySerializer,
    AlertPolicyUpdateSerializer,
    MaintenanceWindowSerializer,
    MaintenanceWindowUpdateSerializer,
)


def _resolve_monitor(project, monitor_id):
    if monitor_id is None:
        return None
    monitor = Monitor.objects.filter(project=project, pk=monitor_id, archived_at__isnull=True).first()
    if monitor is None:
        raise ValidationError({"monitor": ["The monitor does not belong to this project."]})
    return monitor


class AlertPolicyViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    queryset = AlertPolicy.objects.none()
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    capabilities = {
        "retrieve": "alertpolicy.view",
        "partial_update": "alertpolicy.manage",
        "destroy": "alertpolicy.delete",
    }

    def get_queryset(self):
        qs = AlertPolicy.objects.for_user(self.request.user).select_related("project", "monitor")
        if self.action == "list":
            if project := self.request.query_params.get("project"):
                qs = qs.filter(project_id=project)
            if organization := self.request.query_params.get("organization"):
                qs = qs.filter(project__organization_id=organization)
        return qs.order_by("project__name", "name")

    def get_serializer_class(self):
        return AlertPolicyUpdateSerializer if self.action == "partial_update" else AlertPolicySerializer

    def _service_data(self, validated: dict, project) -> dict:
        data = dict(validated)
        data.pop("project_id", None)
        if "monitor_id" in data:
            data["monitor"] = _resolve_monitor(project, data.pop("monitor_id"))
        return data

    def create(self, request, *args, **kwargs):
        serializer = AlertPolicySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        project = get_scoped_or_404(Project.objects.for_user(request.user), serializer.validated_data["project_id"])
        data = self._service_data(serializer.validated_data, project)
        policy = services.save_alert_policy(actor=request.user, project=project, data=data, request=request)
        return Response(AlertPolicySerializer(policy).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        policy = self.get_object()
        serializer = AlertPolicyUpdateSerializer(policy, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = self._service_data(serializer.validated_data, policy.project)
        policy = services.save_alert_policy(
            actor=request.user, project=policy.project, data=data, policy=policy, request=request
        )
        return Response(AlertPolicySerializer(policy).data)

    def perform_destroy(self, instance):
        services.delete_alert_policy(actor=self.request.user, policy=instance, request=self.request)


class MaintenanceWindowViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    queryset = MaintenanceWindow.objects.none()
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    capabilities = {
        "retrieve": "maintenance.view",
        "partial_update": "maintenance.manage",
        "destroy": "maintenance.manage",
    }

    def get_queryset(self):
        qs = MaintenanceWindow.objects.for_user(self.request.user).select_related("project", "monitor")
        if self.action == "list":
            if project := self.request.query_params.get("project"):
                qs = qs.filter(project_id=project)
            if organization := self.request.query_params.get("organization"):
                qs = qs.filter(project__organization_id=organization)
        return qs.order_by("-starts_at")

    def get_serializer_class(self):
        return MaintenanceWindowUpdateSerializer if self.action == "partial_update" else MaintenanceWindowSerializer

    def create(self, request, *args, **kwargs):
        serializer = MaintenanceWindowSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        project = get_scoped_or_404(Project.objects.for_user(request.user), data.pop("project_id"))
        require_capability(request.user, project.organization_id, "maintenance.manage")
        monitor = _resolve_monitor(project, data.pop("monitor_id", None))
        with transaction.atomic():
            window = MaintenanceWindow.objects.create(project=project, monitor=monitor, created_by=request.user, **data)
            record_audit(
                actor=request.user,
                organization=project.organization,
                action="maintenance_window.created",
                resource=window,
                metadata={"title": window.title, "starts_at": str(window.starts_at), "ends_at": str(window.ends_at)},
                request=request,
            )
        return Response(MaintenanceWindowSerializer(window).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        window = self.get_object()
        serializer = MaintenanceWindowUpdateSerializer(window, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        if "monitor_id" in data:
            window.monitor = _resolve_monitor(window.project, data.pop("monitor_id"))
        for field, value in data.items():
            setattr(window, field, value)
        with transaction.atomic():
            window.save()
            record_audit(
                actor=request.user,
                organization=window.project.organization,
                action="maintenance_window.updated",
                resource=window,
                request=request,
            )
        return Response(MaintenanceWindowSerializer(window).data)

    def perform_destroy(self, instance):
        with transaction.atomic():
            record_audit(
                actor=self.request.user,
                organization=instance.project.organization,
                action="maintenance_window.deleted",
                resource=instance,
                metadata={"title": instance.title},
                request=self.request,
            )
            instance.delete()
