from django.apps import AppConfig


class ErrorsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.errors"
    label = "errors"
    verbose_name = "Application errors"
