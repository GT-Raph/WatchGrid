from django.urls import path

from . import views

urlpatterns = [
    path("welcome/", views.welcome, name="welcome"),
    path("invitations/accept/", views.accept_invitation, name="invitation-accept"),
    path("team/", views.team, name="team"),
    path("team/invite/", views.invite, name="team-invite"),
    path("team/<uuid:pk>/<str:action>/", views.member_action, name="member-action"),
    path("settings/organization/", views.organization_form, name="organization-settings"),
    path("settings/organization/archive/", views.organization_archive, name="organization-archive"),
]
