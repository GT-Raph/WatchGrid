from django.urls import path

from . import views

urlpatterns = [
    path("monitors/", views.monitors, name="monitors"),
    path("monitors/new/", views.monitor_form, name="monitor-new"),
    path("monitors/<uuid:pk>/", views.monitor_detail, name="monitor-detail"),
    path("monitors/<uuid:pk>/edit/", views.monitor_form, name="monitor-edit"),
    path("monitors/<uuid:pk>/<str:action>/", views.monitor_action, name="monitor-action"),
]
