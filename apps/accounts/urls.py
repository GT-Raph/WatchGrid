from django.urls import path

from . import views

urlpatterns = [
    path("login/", views.login_page, name="login"),
    path("register/", views.register, name="register"),
    path("logout/", views.logout_page, name="logout"),
    path("forgot-password/", views.recovery, {"mode": "forgot"}, name="forgot-password"),
    path("reset-password/", views.recovery, {"mode": "reset"}, name="reset-password"),
    path("verify-email/", views.recovery, {"mode": "verify"}, name="verify-email"),
    path("settings/", views.settings_page, name="settings"),
]
