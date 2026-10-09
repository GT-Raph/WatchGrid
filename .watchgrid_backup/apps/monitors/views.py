"""Monitor list, detail, create/edit and operator action pages."""

from django.contrib import messages
from django.db.models import Case, IntegerField, Q, Value, When
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from rest_framework.exceptions import APIException

from apps.analytics import services as analytics
from apps.analytics.services import STATUS_PRIORITY
from common.exceptions import DomainError
from common.permissions.tenancy import require_capability
from common.utilities.forms import ConfirmForm, SerializerForm
from common.utilities.time import parse_time_range
from common.utilities.web import page, paginate, render_page, scoped, throttle

from . import services
from .charts import latency_chart
from .models import Monitor
from .serializers import MonitorSerializer, MonitorUpdateSerializer

PRIORITY = Case(
    *[When(status=status, then=Value(rank)) for status, rank in STATUS_PRIORITY.items()],
    default=Value(9),
    output_field=IntegerField(),
)


@page(methods=("GET",))
def monitors(request):
    queryset = scoped(request, Monitor).not_archived().select_related("project")
    if search := request.GET.get("search"):
        queryset = queryset.filter(Q(name__icontains=search) | Q(url__icontains=search))
    if status := request.GET.get("status"):
        queryset = queryset.filter(status=status)
    sort = request.GET.get("sort")
    if sort == "status":
        # Most urgent first: down, failing, recovering, degraded, no data, operational, paused.
        queryset = queryset.annotate(priority=PRIORITY).order_by("priority", "name", "id")
    else:
        queryset = queryset.order_by("-last_checked_at" if sort == "recent" else "name", "id")
    rows = paginate(request, queryset)
    ids = [monitor.pk for monitor in rows]
    uptime = analytics.uptime_for_monitors(ids, parse_time_range({"range": "24h"}))
    recent = analytics.recent_outcomes(ids, 30)
    for monitor in rows:
        monitor.uptime = uptime.get(monitor.pk)
        monitor.recent = recent.get(monitor.pk, [])
    return render_page(
        request,
        "monitors/list.html",
        "Monitors",
        rows=rows,
        show_scope=True,
        status_choices=Monitor.Status.choices,
    )


@page()
def monitor_form(request, pk=None):
    monitor = get_object_or_404(scoped(request, Monitor), pk=pk) if pk else None
    require_capability(request.user, request.web_scope["organization"].pk, "monitor.manage")
    projects = request.web_scope["projects"]
    form = SerializerForm(
        MonitorUpdateSerializer if monitor else MonitorSerializer,
        request.POST if request.method == "POST" else None,
        instance=monitor,
        request=request,
        choices={"project": [(str(p.pk), p.name) for p in projects]},
        initial={"project": str(getattr(request.web_scope["project"], "pk", ""))},
    )
    if form.is_valid():
        try:
            data = dict(form.validated_data)
            if monitor:
                result = services.update_monitor(actor=request.user, monitor=monitor, data=data, request=request)
            else:
                project = get_object_or_404(projects, pk=data.pop("project_id"))
                result = services.create_monitor(actor=request.user, project=project, data=data, request=request)
            messages.success(request, "Monitor saved.")
            return redirect("web:monitor-detail", pk=result.pk)
        except (DomainError, APIException) as exc:
            form.service_error(exc)
    return render_page(
        request,
        "form_page.html",
        "Edit monitor" if monitor else "Add a monitor",
        section="monitors",
        form=form,
        submit_label="Save monitor",
        cancel_url=reverse("web:monitor-detail", args=[monitor.pk]) if monitor else reverse("web:monitors"),
        description=(
            "Choose a public URL and how WatchGrid should check it. Monitoring continues when you close this page."
        ),
    )


@page(methods=("GET",))
def monitor_detail(request, pk):
    monitor = get_object_or_404(scoped(request, Monitor).select_related("project"), pk=pk)
    time_range = parse_time_range(request.GET)
    series = analytics.monitor_series(monitor, time_range)
    checks = monitor.checks.filter(checked_at__gte=time_range.start, checked_at__lt=time_range.end).order_by(
        "-checked_at", "-id"
    )
    return render_page(
        request,
        "monitors/detail.html",
        monitor.name,
        section="monitors",
        monitor=monitor,
        stats=analytics.monitor_statistics(monitor, time_range),
        chart=latency_chart(series, time_range.start, time_range.end, monitor.response_time_threshold_ms),
        series=series,
        rows=paginate(request, checks),
        incidents=monitor.incidents.order_by("-detected_at")[:10],
        show_range=True,
    )


MONITOR_ACTIONS = {
    "pause": (services.pause_monitor, "Pause monitor", "monitor.operate"),
    "resume": (services.resume_monitor, "Resume monitor", "monitor.operate"),
    "test": (services.request_manual_check, "Run a check", "monitor.operate"),
    "archive": (services.archive_monitor, "Archive monitor", "monitor.manage"),
}


@page()
def monitor_action(request, pk, action):
    monitor = get_object_or_404(scoped(request, Monitor), pk=pk)
    if action not in MONITOR_ACTIONS:
        raise Http404
    operation, label, capability = MONITOR_ACTIONS[action]
    require_capability(request.user, monitor.organization_id, capability)
    # Archiving is the only destructive action, so it alone asks for confirmation.
    if request.method == "POST" and (action != "archive" or ConfirmForm(request.POST).is_valid()):
        if action == "test":
            throttle(request, "manual_check")
        operation(actor=request.user, monitor=monitor, request=request)
        messages.success(
            request, "Check queued. Its result appears in check history." if action == "test" else "Monitor updated."
        )
        return redirect("web:monitors") if action == "archive" else redirect("web:monitor-detail", pk=pk)
    return render_page(
        request,
        "form_page.html",
        label,
        section="monitors",
        form=ConfirmForm(),
        submit_label=label,
        description=f"{label} for {monitor.name}. History is retained.",
        cancel_url=reverse("web:monitor-detail", args=[pk]),
    )
