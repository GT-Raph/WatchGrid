from django.urls import path

from . import views

urlpatterns = [
    path("incidents/", views.incidents, name="incidents"),
    path("incidents/<uuid:pk>/", views.incident_detail, name="incident-detail"),
]
