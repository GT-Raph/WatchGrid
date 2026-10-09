from django.urls import path

from . import views

urlpatterns = [
    path("projects/", views.projects, name="projects"),
    path("projects/new/", views.project_form, name="project-new"),
    path("projects/<uuid:pk>/edit/", views.project_form, name="project-edit"),
    path("projects/<uuid:pk>/<str:action>/", views.project_action, name="project-action"),
]
