from django.urls import path

from . import views

urlpatterns = [
    path("settings/audit/", views.audit, name="audit"),
]
