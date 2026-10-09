from django.conf import settings
from django.db import models
from django.db.models import Q

from common.constants import Role
from common.utilities.models import UUIDModel


class OrganizationQuerySet(models.QuerySet):
    def active(self):
        return self.filter(archived_at__isnull=True)

    def for_user(self, user):
        if not getattr(user, "is_authenticated", False):
            return self.none()
        # Subquery rather than a join so later annotations over memberships are not
        # silently restricted to the requesting user's row.
        member_of = Membership.objects.filter(user=user).values("organization_id")
        return self.active().filter(id__in=member_of)


class Organization(UUIDModel):
    """Primary tenant boundary (Section 23)."""

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=80, unique=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owned_organizations")
    archived_at = models.DateTimeField(null=True, blank=True)

    objects = OrganizationQuerySet.as_manager()

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Membership(UUIDModel):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.VIEWER)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "user"], name="organizations_membership_unique_user"),
        ]
        indexes = [models.Index(fields=["user", "organization"])]

    def __str__(self) -> str:
        return f"{self.user} in {self.organization} ({self.role})"


class Invitation(UUIDModel):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField(max_length=254)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.VIEWER)
    token_hash = models.CharField(max_length=64, unique=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="sent_invitations"
    )
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "email"],
                condition=Q(accepted_at__isnull=True, revoked_at__isnull=True),
                name="organizations_invitation_one_pending_per_email",
            )
        ]

    @property
    def is_pending(self) -> bool:
        from django.utils import timezone

        return self.accepted_at is None and self.revoked_at is None and self.expires_at > timezone.now()
