"""Project list and project create/edit/archive pages."""

from django.contrib import messages
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse

from common.exceptions import DomainError
from common.permissions.tenancy import require_capability
from common.utilities.forms import ConfirmForm, SerializerForm
from common.utilities.web import page, paginate, render_page

from . import services
from .models import Project
from .serializers import ProjectSerializer, ProjectUpdateSerializer


def _form_page(request, title, form, description):
    return render_page(
        request,
        "form_page.html",
        title,
        form=form,
        section="projects",
        submit_label="Save changes",
        cancel_url=reverse("web:projects"),
        description=description,
    )


@page(methods=("GET",))
def projects(request):
    queryset = Project.objects.for_user(request.user).filter(organization=request.web_scope["organization"])
    selected = request.GET.get("status", "active")
    if selected != "all":
        queryset = queryset.filter(status=selected)
    if search := request.GET.get("search"):
        queryset = queryset.filter(name__icontains=search)
    queryset = queryset.annotate(
        monitor_count=Count("monitors", filter=Q(monitors__archived_at__isnull=True), distinct=True)
    )
    return render_page(
        request,
        "projects/list.html",
        "Projects",
        rows=paginate(request, queryset.order_by("name")),
        selected_status=selected,
        show_scope=True,
    )


@page()
def project_form(request, pk=None):
    organization = request.web_scope["organization"]
    project = (
        get_object_or_404(Project.objects.for_user(request.user), organization=organization, pk=pk) if pk else None
    )
    require_capability(request.user, organization.pk, "project.edit" if project else "project.create")
    form = SerializerForm(
        ProjectUpdateSerializer if project else ProjectSerializer,
        request.POST if request.method == "POST" else None,
        instance=project,
        request=request,
        fields=("name", "description", "environment", "organization"),
        initial={"organization": str(organization.pk)},
        hidden=("organization",),
    )
    if form.is_valid():
        try:
            if project:
                services.update_project(actor=request.user, project=project, data=form.validated_data, request=request)
            else:
                # The organization comes from the authorized scope, never from the hidden input.
                services.create_project(
                    actor=request.user, organization=organization, data=form.validated_data, request=request
                )
            messages.success(request, "Project saved.")
            return redirect("web:projects")
        except DomainError as exc:
            form.service_error(exc)
    return _form_page(
        request,
        "Edit project" if project else "Create a project",
        form,
        "Group related services and their alert policies in one project.",
    )


@page()
def project_action(request, pk, action):
    project = get_object_or_404(
        Project.objects.for_user(request.user), pk=pk, organization=request.web_scope["organization"]
    )
    if action not in ("archive", "restore"):
        raise Http404
    require_capability(request.user, project.organization_id, "project.archive")
    form = ConfirmForm(request.POST if request.method == "POST" else None)
    if form.is_valid():
        operation = services.archive_project if action == "archive" else services.restore_project
        operation(actor=request.user, project=project, request=request)
        if request.session.get("project") == str(project.pk):
            request.session["project"] = ""
        messages.success(request, "Project updated. Restored monitors stay paused until you resume them.")
        return redirect("web:projects")
    return _form_page(
        request,
        f"{action.capitalize()} project",
        form,
        f"{project.name}: archiving stops checks and retains history.",
    )
