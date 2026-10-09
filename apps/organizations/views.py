"""Organization onboarding, team membership and organization settings pages."""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from rest_framework.exceptions import APIException

from apps.accounts.serializers import TokenSerializer
from common.exceptions import DomainError
from common.permissions.tenancy import has_capability, require_capability
from common.utilities.forms import ConfirmForm, SerializerForm
from common.utilities.web import page, paginate, render_page

from . import services
from .models import Invitation, Membership
from .serializers import InvitationCreateSerializer, MembershipRoleSerializer, OrganizationSerializer


def _data(request):
    return request.POST if request.method == "POST" else None


def _form_page(request, title, form, *, section, description=""):
    """Generic form page; ``section`` is both the highlighted nav item and the cancel target."""
    return render_page(
        request,
        "form_page.html",
        title,
        form=form,
        section=section,
        submit_label="Save changes",
        cancel_url=reverse(f"web:{section}"),
        description=description,
    )


@page(organization=False)
def welcome(request):
    form = SerializerForm(OrganizationSerializer, _data(request), fields=("name",), request=request)
    if form.is_valid():
        organization = services.create_organization(user=request.user, request=request, **form.validated_data)
        request.session["organization"] = str(organization.pk)
        request.session["project"] = ""
        messages.success(request, "Organization created. Add a project to get started.")
        return redirect("web:projects")
    return render_page(
        request,
        "accounts/auth.html",
        "Create an organization",
        form=form,
        subtitle="A shared home for your projects, monitors, and team.",
        submit_label="Create organization",
    )


@page(organization=False)
def accept_invitation(request):
    form = SerializerForm(
        TokenSerializer,
        _data(request),
        initial={"token": request.GET.get("token", "")},
        hidden=("token",),
    )
    if form.is_valid():
        try:
            membership = services.accept_invitation(user=request.user, request=request, **form.validated_data)
            request.session["organization"] = str(membership.organization_id)
            request.session["project"] = ""
            messages.success(request, "Invitation accepted.")
            return redirect("web:overview")
        except (DomainError, APIException) as exc:
            form.service_error(exc)
    return render_page(
        request,
        "accounts/auth.html",
        "Join your team",
        form=form,
        submit_label="Accept invitation",
        subtitle=f"Accept this invitation as {request.user.email}.",
    )


@page(methods=("GET",))
def team(request):
    organization = request.web_scope["organization"]
    invitations = (
        organization.invitations.filter(accepted_at__isnull=True, revoked_at__isnull=True)
        if has_capability(request.user, organization.pk, "members.manage")
        else []
    )
    return render_page(
        request,
        "organizations/team.html",
        "Team",
        rows=paginate(request, organization.memberships.select_related("user").order_by("created_at")),
        invitations=invitations,
        show_scope=True,
    )


@page()
def invite(request):
    organization = request.web_scope["organization"]
    require_capability(request.user, organization.pk, "members.manage")
    form = SerializerForm(InvitationCreateSerializer, _data(request))
    if form.is_valid():
        try:
            services.invite_member(
                actor=request.user, organization=organization, request=request, **form.validated_data
            )
            messages.success(request, "Invitation sent.")
            return redirect("web:team")
        except (DomainError, APIException) as exc:
            form.service_error(exc)
    return _form_page(request, "Invite a teammate", form, section="team")


MEMBER_ACTIONS = {"role": "Change role", "remove": "Remove member", "revoke-invitation": "Revoke invitation"}


@page()
def member_action(request, pk, action):
    organization = request.web_scope["organization"]
    require_capability(request.user, organization.pk, "members.manage")
    if action == "revoke-invitation":
        target = get_object_or_404(Invitation, pk=pk, organization=organization)
    elif action in ("role", "remove"):
        target = get_object_or_404(
            Membership.objects.select_related("user", "organization"), pk=pk, organization=organization
        )
    else:
        raise Http404
    data = _data(request)
    form = (
        SerializerForm(MembershipRoleSerializer, data, initial={"role": target.role})
        if action == "role"
        else ConfirmForm(data)
    )
    if form.is_valid():
        try:
            if action == "role":
                services.change_member_role(
                    actor=request.user, membership=target, request=request, **form.validated_data
                )
            elif action == "remove":
                services.remove_member(actor=request.user, membership=target, request=request)
            else:
                services.revoke_invitation(actor=request.user, invitation=target, request=request)
            messages.success(request, "Team updated.")
            return redirect("web:team")
        except (DomainError, APIException) as exc:
            form.add_error(None, str(exc))
    return _form_page(request, MEMBER_ACTIONS[action], form, section="team")


@page()
def organization_form(request):
    organization = request.web_scope["organization"]
    require_capability(request.user, organization.pk, "org.manage")
    form = SerializerForm(
        OrganizationSerializer, _data(request), instance=organization, fields=("name", "slug"), request=request
    )
    if form.is_valid():
        try:
            services.update_organization(
                user=request.user, organization=organization, data=form.validated_data, request=request
            )
            messages.success(request, "Organization saved.")
            return redirect("web:settings")
        except DomainError as exc:
            form.service_error(exc)
    return _form_page(request, "Organization settings", form, section="settings")


@page()
def organization_archive(request):
    organization = request.web_scope["organization"]
    require_capability(request.user, organization.pk, "org.archive")
    form = ConfirmForm(_data(request))
    if form.is_valid():
        services.archive_organization(user=request.user, organization=organization, request=request)
        request.session.pop("organization", None)
        request.session.pop("project", None)
        messages.success(request, "Organization archived.")
        return redirect("web:overview")
    return _form_page(
        request,
        "Archive organization",
        form,
        section="settings",
        description=f"Archive {organization.name} and stop all its monitors. Its history is retained.",
    )
