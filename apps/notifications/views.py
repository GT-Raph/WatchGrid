"""Integrations page: alert policies and maintenance windows (project keys live in api_keys)."""

from django.contrib import messages
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from rest_framework.exceptions import APIException

from apps.api_keys.models import APIKey
from apps.audit.services import record_audit
from apps.monitors.models import Monitor
from common.exceptions import DomainError
from common.permissions.tenancy import has_capability, require_capability
from common.utilities.forms import ConfirmForm, SerializerForm
from common.utilities.web import page, render_page, scoped

from . import services
from .forms import AlertPolicyForm
from .models import AlertPolicy, MaintenanceWindow
from .serializers import (
    AlertPolicySerializer,
    AlertPolicyUpdateSerializer,
    MaintenanceWindowSerializer,
    MaintenanceWindowUpdateSerializer,
)

KINDS = ("policies", "maintenance")


def _form_page(request, title, form, description):
    return render_page(
        request,
        "form_page.html",
        title,
        form=form,
        section="integrations",
        submit_label="Save changes",
        cancel_url=reverse("web:integrations"),
        description=description,
    )


@page(methods=("GET",))
def integrations(request):
    can_view_keys = has_capability(request.user, request.web_scope["organization"].pk, "apikey.view")
    return render_page(
        request,
        "notifications/integrations.html",
        "Integrations",
        show_scope=True,
        keys=scoped(request, APIKey).select_related("project").order_by("-created_at") if can_view_keys else [],
        policies=scoped(request, AlertPolicy).select_related("project", "monitor"),
        windows=scoped(request, MaintenanceWindow).select_related("project", "monitor"),
    )


@page()
def notification_form(request, kind, pk=None):
    if kind not in KINDS:
        raise Http404
    is_policy = kind == "policies"
    model = AlertPolicy if is_policy else MaintenanceWindow
    require_capability(
        request.user,
        request.web_scope["organization"].pk,
        "alertpolicy.manage" if is_policy else "maintenance.manage",
    )
    instance = get_object_or_404(scoped(request, model).select_related("project"), pk=pk) if pk else None
    projects = request.web_scope["projects"]
    if is_policy:
        serializer = AlertPolicyUpdateSerializer if pk else AlertPolicySerializer
    else:
        serializer = MaintenanceWindowUpdateSerializer if pk else MaintenanceWindowSerializer
    monitors = scoped(request, Monitor).not_archived()
    if instance:
        monitors = monitors.filter(project=instance.project)
    form = (AlertPolicyForm if is_policy else SerializerForm)(
        serializer,
        request.POST if request.method == "POST" else None,
        instance=instance,
        request=request,
        choices={
            "project": [(str(p.pk), p.name) for p in projects],
            "monitor": [("", "All monitors in project"), *[(str(m.pk), m.name) for m in monitors]],
        },
        initial=None
        if pk
        else {
            "event_types": ["monitor_down", "monitor_recovered"],
            "channels": [{"type": "email", "to_roles": ["owner", "admin"]}],
        },
    )
    if form.is_valid():
        try:
            data = dict(form.validated_data)
            project = instance.project if instance else get_object_or_404(projects, pk=data.pop("project_id"))
            monitor_id = data.pop("monitor_id", None)
            data["monitor"] = get_object_or_404(monitors, pk=monitor_id, project=project) if monitor_id else None
            if is_policy:
                services.save_alert_policy(
                    actor=request.user, project=project, data=data, policy=instance, request=request
                )
            else:
                _save_maintenance_window(request, project, instance, data)
            messages.success(request, "Alert policy saved." if is_policy else "Maintenance window saved.")
            return redirect("web:integrations")
        except (DomainError, APIException) as exc:
            form.service_error(exc)
    return _form_page(
        request,
        ("Edit " if pk else "Create ") + ("alert policy" if is_policy else "maintenance window"),
        form,
        "Times use your account time zone. Maintenance keeps checks running and suppresses outage alerts.",
    )


def _save_maintenance_window(request, project, instance, data):
    created = instance is None
    with transaction.atomic():
        if created:
            instance = MaintenanceWindow.objects.create(project=project, created_by=request.user, **data)
        else:
            for field, value in data.items():
                setattr(instance, field, value)
            instance.save()
        record_audit(
            actor=request.user,
            organization=project.organization,
            action="maintenance_window.created" if created else "maintenance_window.updated",
            resource=instance,
            request=request,
        )


@page()
def notification_delete(request, kind, pk):
    if kind not in KINDS:
        raise Http404
    is_policy = kind == "policies"
    instance = get_object_or_404(scoped(request, AlertPolicy if is_policy else MaintenanceWindow), pk=pk)
    require_capability(
        request.user,
        instance.project.organization_id,
        "alertpolicy.delete" if is_policy else "maintenance.manage",
    )
    form = ConfirmForm(request.POST if request.method == "POST" else None)
    if form.is_valid():
        if is_policy:
            services.delete_alert_policy(actor=request.user, policy=instance, request=request)
        else:
            with transaction.atomic():
                record_audit(
                    actor=request.user,
                    organization=instance.project.organization,
                    action="maintenance_window.deleted",
                    resource=instance,
                    request=request,
                )
                instance.delete()
        messages.success(request, "Deleted.")
        return redirect("web:integrations")
    return _form_page(
        request,
        "Delete alert policy" if is_policy else "Delete maintenance window",
        form,
        "This action cannot be undone.",
    )
