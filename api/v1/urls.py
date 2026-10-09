"""Version 1 of the WatchGrid REST API, mounted at /api/v1/."""

from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.accounts import api as accounts
from apps.analytics.api import DashboardView, StatusGridView
from apps.checks.api import PlatformStatusView
from apps.errors.api import ErrorIngestView
from apps.organizations.api import AcceptInvitationView

from .router import router

auth_urlpatterns = [
    path("auth/csrf/", accounts.CsrfView.as_view(), name="auth-csrf"),
    path("auth/register/", accounts.RegisterView.as_view(), name="auth-register"),
    path("auth/login/", accounts.LoginView.as_view(), name="auth-login"),
    path("auth/logout/", accounts.LogoutView.as_view(), name="auth-logout"),
    path("auth/me/", accounts.MeView.as_view(), name="auth-me"),
    path("auth/password-change/", accounts.PasswordChangeView.as_view(), name="auth-password-change"),
    path("auth/password-reset/", accounts.PasswordResetRequestView.as_view(), name="auth-password-reset"),
    path(
        "auth/password-reset/confirm/",
        accounts.PasswordResetConfirmView.as_view(),
        name="auth-password-reset-confirm",
    ),
    path("auth/verify-email/", accounts.VerifyEmailView.as_view(), name="auth-verify-email"),
    path("auth/verify-email/resend/", accounts.ResendVerificationView.as_view(), name="auth-verify-email-resend"),
]

urlpatterns = [
    *auth_urlpatterns,
    path("invitations/accept/", AcceptInvitationView.as_view(), name="invitation-accept"),
    path("events/errors/", ErrorIngestView.as_view(), name="error-ingest"),
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    path("dashboard/grid/", StatusGridView.as_view(), name="dashboard-grid"),
    path("platform/status/", PlatformStatusView.as_view(), name="platform-status"),
    path("schema/", SpectacularAPIView.as_view(), name="api-schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="api-schema"), name="api-docs"),
    *router.urls,
]
