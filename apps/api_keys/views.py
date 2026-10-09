"""Project key pages: create (secret shown once) and revoke."""

from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse

from common.permissions.tenancy import require_capability
from common.utilities.forms import ConfirmForm, SerializerForm
from common.utilities.web import page, render_page, scoped

from . import services
from .models import APIKey
from .serializers import APIKeyCreateSerializer


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


@page()
def key_create(request):
    require_capability(request.user, request.web_scope["organization"].pk, "apikey.manage")
    projects = request.web_scope["projects"]
    form = SerializerForm(APIKeyCreateSerializer, request.POST if request.method == "POST" else None)
    form.fields["project"] = forms.ChoiceField(choices=[(str(p.pk), p.name) for p in projects])
    if form.is_valid():
        project = get_object_or_404(projects, pk=form.cleaned_data["project"])
        _, raw_key = services.create_api_key(
            actor=request.user, project=project, request=request, **form.validated_data
        )
        return render_page(
            request, "api_keys/key_created.html", "Your project key", section="integrations", raw_key=raw_key
        )
    return _form_page(
        request,
        "Create a project key",
        form,
        "Use this key to send application errors. The full key is shown only once.",
    )


@page()
def key_revoke(request, pk):
    key = get_object_or_404(scoped(request, APIKey).select_related("project"), pk=pk)
    require_capability(request.user, key.project.organization_id, "apikey.manage")
    form = ConfirmForm(request.POST if request.method == "POST" else None)
    if form.is_valid():
        services.revoke_api_key(actor=request.user, api_key=key, request=request)
        messages.success(request, "Project key revoked.")
        return redirect("web:integrations")
    return _form_page(
        request,
        "Revoke project key",
        form,
        f"Applications using {key.name} will no longer be able to send errors with this key.",
    )
