"""Cross-app constants (kept free of model imports to avoid cycles)."""

from django.db import models


class Role(models.TextChoices):
    OWNER = "owner", "Owner"
    ADMIN = "admin", "Administrator"
    DEVELOPER = "developer", "Developer"
    VIEWER = "viewer", "Viewer"


ROLE_RANK = {Role.VIEWER: 0, Role.DEVELOPER: 1, Role.ADMIN: 2, Role.OWNER: 3}


class Severity(models.TextChoices):
    INFO = "info", "Info"
    WARNING = "warning", "Warning"
    MINOR = "minor", "Minor"
    MAJOR = "major", "Major"
    CRITICAL = "critical", "Critical"


SEVERITY_RANK = {
    Severity.INFO: 0,
    Severity.WARNING: 1,
    Severity.MINOR: 2,
    Severity.MAJOR: 3,
    Severity.CRITICAL: 4,
}


class Environment(models.TextChoices):
    PRODUCTION = "production", "Production"
    STAGING = "staging", "Staging"
    DEVELOPMENT = "development", "Development"
    OTHER = "other", "Other"
