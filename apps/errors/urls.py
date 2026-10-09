from django.urls import path

from . import views

urlpatterns = [
    path("errors/", views.errors, name="errors"),
    path("errors/<uuid:pk>/", views.error_detail, name="error-detail"),
]
