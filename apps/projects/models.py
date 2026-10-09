from django.conf import settings
from django.db import models

from common.constants import Environment
from common.utilities.models import UUIDModel


class ProjectQuerySet(models.QuerySet):
    def for_user(self, user):
        if not getattr(user, "is_authenticated", False):
            return self.none()
        return self.filter(
            organization__memberships__user=user,
            organization__archived_at__isnull=True,
        )

    def active(self):
        return self.filter(status=Project.Status.ACTIVE)


class Project(UUIDModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="projects")
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=80)
    description = models.TextField(blank=True, max_length=2000)
    environment = models.CharField(max_length=16, choices=Environment.choices, default=Environment.PRODUCTION)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    objects = ProjectQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "slug"], name="projects_unique_slug_per_org"),
        ]
        indexes = [models.Index(fields=["organization", "status"])]

    def __str__(self) -> str:
        return self.name
