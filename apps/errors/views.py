"""Grouped application errors and error-group detail pages."""

from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect

from common.permissions.tenancy import require_capability
from common.utilities.web import page, paginate, render_page, scoped

from . import services
from .models import ErrorGroup
from .serializers import ErrorGroupStatusSerializer


@page(methods=("GET",))
def errors(request):
    queryset = scoped(request, ErrorGroup).select_related("project")
    selected = request.GET.get("status", "unresolved")
    if selected != "all":
        queryset = queryset.filter(status=selected)
    if search := request.GET.get("search"):
        queryset = queryset.filter(Q(error_type__icontains=search) | Q(normalized_message__icontains=search))
    return render_page(
        request,
        "errors/list.html",
        "Application errors",
        section="errors",
        rows=paginate(request, queryset.order_by("-last_seen")),
        show_scope=True,
        selected_status=selected,
    )


@page()
def error_detail(request, pk):
    group = get_object_or_404(scoped(request, ErrorGroup).select_related("project"), pk=pk)
    if request.method == "POST":
        require_capability(request.user, group.project.organization_id, "errors.manage")
        serializer = ErrorGroupStatusSerializer(data=request.POST)
        serializer.is_valid(raise_exception=True)
        services.set_status(actor=request.user, group=group, request=request, **serializer.validated_data)
        messages.success(request, "Error status updated.")
        return redirect("web:error-detail", pk=pk)
    return render_page(
        request,
        "errors/detail.html",
        group.error_type,
        section="errors",
        group=group,
        rows=paginate(request, group.events.order_by("-occurred_at")),
        statuses=ErrorGroup.Status.choices,
    )
