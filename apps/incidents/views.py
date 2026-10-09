"""Incident list and incident detail (timeline, evidence, operator actions) pages."""

from django.contrib import messages
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.utils.dateparse import parse_datetime

from common.exceptions import DomainError
from common.permissions.tenancy import require_capability
from common.utilities.forms import NoteForm, SerializerForm
from common.utilities.web import page, paginate, render_page, scoped

from . import services
from .models import Incident
from .serializers import IncidentStatusSerializer


@page(methods=("GET",))
def incidents(request):
    queryset = scoped(request, Incident).select_related("project", "monitor")
    selected = request.GET.get("status", "active")
    if selected == "active":
        queryset = queryset.active()
    elif selected and selected != "all":
        queryset = queryset.filter(status=selected)
    if search := request.GET.get("search"):
        queryset = queryset.filter(Q(title__icontains=search) | Q(summary__icontains=search))
    return render_page(
        request,
        "incidents/list.html",
        "Incidents",
        rows=paginate(request, queryset.order_by("-detected_at")),
        show_scope=True,
        selected_status=selected,
    )


@page()
def incident_detail(request, pk):
    incident = get_object_or_404(scoped(request, Incident).select_related("project", "monitor"), pk=pk)
    action = request.POST.get("action", "note")
    posted = request.method == "POST"
    note_form = NoteForm(request.POST if posted and action == "note" else None)
    status_form = SerializerForm(
        IncidentStatusSerializer,
        request.POST if posted and action == "status" else None,
        initial={"status": incident.status},
    )
    if posted:
        require_capability(request.user, incident.project.organization_id, "incident.manage")
        if action not in ("note", "status"):
            raise Http404
        form = note_form if action == "note" else status_form
        if form.is_valid():
            try:
                if action == "note":
                    services.add_note(actor=request.user, incident=incident, message=form.cleaned_data["message"])
                else:
                    services.change_status(
                        actor=request.user, incident=incident, request=request, **form.validated_data
                    )
                messages.success(request, "Incident updated.")
                return redirect("web:incident-detail", pk=pk)
            except DomainError as exc:
                form.add_error(None, exc.message)
    evidence = incident.evidence or {}
    return render_page(
        request,
        "incidents/detail.html",
        incident.title,
        section="incidents",
        incident=incident,
        note_form=note_form,
        status_form=status_form,
        events=incident.events.select_related("actor"),
        notifications=incident.notifications.order_by("-created_at")[:50],
        failure_samples=_samples(evidence.get("samples")),
        recovery_samples=_samples(evidence.get("recovery_samples")),
    )


def _samples(stored) -> list[dict]:
    """Evidence samples newest first, with timestamps parsed for display."""
    return [{**sample, "checked_at": parse_datetime(sample["checked_at"])} for sample in reversed(stored or [])]
