"""Sign-in, registration, account recovery and profile pages."""

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.http import Http404
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme

from common.exceptions import DomainError
from common.utilities.forms import SerializerForm
from common.utilities.web import page, render_page, throttle

from . import serializers, services

AUTH_TEMPLATE = "accounts/auth.html"


def safe_next(request) -> str:
    """Only follow same-site ``next`` targets (no open redirects)."""
    target = request.POST.get("next", request.GET.get("next", ""))
    if url_has_allowed_host_and_scheme(target, {request.get_host()}, require_https=request.is_secure()):
        return target
    return "/"


def _data(request):
    return request.POST if request.method == "POST" else None


@page(public=True, organization=False)
def login_page(request):
    if request.user.is_authenticated:
        return redirect(safe_next(request))
    form = SerializerForm(serializers.LoginSerializer, _data(request))
    if request.method == "POST":
        throttle(request, "login")
        if form.is_valid():
            user = authenticate(
                request,
                username=form.validated_data["email"].strip().lower(),
                password=form.validated_data["password"],
            )
            if user:
                login(request, user)
                return redirect(safe_next(request))
            form.add_error(None, "Invalid email or password.")
    return render_page(
        request,
        AUTH_TEMPLATE,
        "Welcome back",
        form=form,
        submit_label="Sign in",
        subtitle="Your services, in one clear view.",
        next_url=safe_next(request),
        auth_mode="login",
    )


@page(public=True, organization=False)
def register(request):
    form = SerializerForm(serializers.RegisterSerializer, _data(request))
    form.fields["password"].widget.attrs["autocomplete"] = "new-password"
    if request.method == "POST":
        throttle(request, "registration")
        if form.is_valid():
            try:
                user = services.register_user(**form.validated_data)
                login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                messages.success(request, "Account created. Check your email for a verification link.")
                return redirect(safe_next(request))
            except DomainError as exc:
                form.service_error(exc)
    return render_page(
        request,
        AUTH_TEMPLATE,
        "Create your account",
        form=form,
        submit_label="Create account",
        subtitle="Keep an eye on what keeps your business running.",
        auth_mode="register",
        next_url=safe_next(request),
    )


@page(organization=False, methods=("POST",))
def logout_page(request):
    logout(request)
    return redirect("web:login")


RECOVERY = {
    "forgot": (serializers.PasswordResetRequestSerializer, "Reset your password", "Send reset link"),
    "reset": (serializers.PasswordResetConfirmSerializer, "Choose a new password", "Save password"),
    "verify": (serializers.TokenSerializer, "Verify your email", "Verify email"),
}


@page(public=True, organization=False)
def recovery(request, mode):
    serializer, title, label = RECOVERY[mode]
    initial = {key: request.GET.get(key, "") for key in ("uid", "token")}
    form = SerializerForm(serializer, _data(request), initial=initial, hidden=("uid", "token"))
    if request.method == "POST":
        throttle(request, "password_reset")
        if form.is_valid():
            try:
                if mode == "forgot":
                    services.request_password_reset(**form.validated_data)
                    messages.success(request, "If an account exists for that address, a reset link has been sent.")
                elif mode == "reset":
                    services.confirm_password_reset(**form.validated_data)
                    messages.success(request, "Password saved. You can now sign in.")
                else:
                    services.verify_email(**form.validated_data)
                    messages.success(request, "Email verified.")
                return redirect("web:login")
            except DomainError as exc:
                form.service_error(exc)
    return render_page(
        request,
        AUTH_TEMPLATE,
        title,
        form=form,
        submit_label=label,
        subtitle="Secure access to your WatchGrid account.",
    )


@page()
def settings_page(request):
    action = request.POST.get("action", "profile")
    if action not in ("profile", "password", "resend"):
        raise Http404
    if request.method == "POST" and action == "resend":
        throttle(request, "password_reset")
        if not request.user.is_email_verified:
            services.send_verification_email(request.user)
        messages.success(request, "Verification email requested.")
        return redirect("web:settings")

    serializer = serializers.PasswordChangeSerializer if action == "password" else serializers.UserSerializer
    form = SerializerForm(
        serializer,
        _data(request),
        instance=request.user if action == "profile" else None,
        request=request,
    )
    if form.is_valid():
        if action == "password":
            request.user.set_password(form.validated_data["new_password"])
            request.user.save(update_fields=["password"])
            update_session_auth_hash(request, request.user)
        else:
            form.serializer.save()
        messages.success(request, "Settings saved.")
        return redirect("web:settings")

    profile_form = form if action == "profile" else SerializerForm(serializers.UserSerializer, instance=request.user)
    password_form = (
        form if action == "password" else SerializerForm(serializers.PasswordChangeSerializer, request=request)
    )
    return render_page(
        request,
        "accounts/settings.html",
        "Settings",
        section="settings",
        profile_form=profile_form,
        password_form=password_form,
    )
