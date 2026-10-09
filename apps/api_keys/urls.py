from django.urls import path

from . import views

# Included before the notifications routes so "keys" is never read as a notification kind.
urlpatterns = [
    path("integrations/keys/new/", views.key_create, name="key-new"),
    path("integrations/keys/<uuid:pk>/revoke/", views.key_revoke, name="key-revoke"),
]
