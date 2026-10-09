from django.urls import path

from . import views

urlpatterns = [
    path("integrations/", views.integrations, name="integrations"),
    path("integrations/<str:kind>/new/", views.notification_form, name="notification-new"),
    path("integrations/<str:kind>/<uuid:pk>/edit/", views.notification_form, name="notification-edit"),
    path("integrations/<str:kind>/<uuid:pk>/delete/", views.notification_delete, name="notification-delete"),
]
