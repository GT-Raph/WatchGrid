"""Organization, membership and invitation workflows (FR-ORG-001, FR-ORG-002)."""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.audit.services import record_audit
from common.constants import Role
from common.exceptions import Conflict, PermissionDeniedError, ValidationFailed
from common.permissions.tenancy import clear_role_cache, require_capability

from .models import Invitation, Membership, Organization


def _unique_slug(base: str) -> str:
    root = slugify(base)[:60] or "org"
    slug = root
    while Organization.objects.filter(slug=slug).exists():
        slug = f"{root}-{secrets.token_hex(3)}"
    return slug


def create_organization(*, user, name: str, slug: str | None = None, request=None) -> Organization:
    with transaction.atomic():
        if slug:
            slug = slugify(slug)[:80]
            if Organization.objects.filter(slug=slug).exists():
                raise Conflict("That organization slug is already taken.", code="SLUG_TAKEN")
        organization = Organization.objects.create(name=name.strip(), slug=slug or _unique_slug(name), owner=user)
        Membership.objects.create(organization=organization, user=user, role=Role.OWNER)
        record_audit(
            actor=user,
            organization=organization,
            action="organization.created",
            resource=organization,
            metadata={"name": organization.name},
            request=request,
        )
    clear_role_cache(user)
    return organization


def update_organization(*, user, organization: Organization, data: dict, request=None) -> Organization:
    require_capability(user, organization.id, "org.manage")
    changes = {}
    if "name" in data and data["name"] != organization.name:
        changes["name"] = [organization.name, data["name"]]
        organization.name = data["name"].strip()
    if "slug" in data and data["slug"] != organization.slug:
        new_slug = slugify(data["slug"])[:80]
        if Organization.objects.exclude(pk=organization.pk).filter(slug=new_slug).exists():
            raise Conflict("That organization slug is already taken.", code="SLUG_TAKEN")
        changes["slug"] = [organization.slug, new_slug]
        organization.slug = new_slug
    if changes:
        organization.save(update_fields=["name", "slug", "updated_at"])
        record_audit(
            actor=user,
            organization=organization,
            action="organization.updated",
            resource=organization,
            metadata={"changes": changes},
            request=request,
        )
    return organization


def archive_organization(*, user, organization: Organization, request=None) -> None:
    require_capability(user, organization.id, "org.archive")
    from apps.monitors.models import Monitor

    with transaction.atomic():
        organization.archived_at = timezone.now()
        organization.save(update_fields=["archived_at", "updated_at"])
        Monitor.objects.filter(project__organization=organization).update(is_active=False, status=Monitor.Status.PAUSED)
        record_audit(
            actor=user,
            organization=organization,
            action="organization.archived",
            resource=organization,
            request=request,
        )


# --------------------------------------------------------------------------------------
# Membership
# --------------------------------------------------------------------------------------


def _owner_count(organization: Organization) -> int:
    return organization.memberships.filter(role=Role.OWNER).count()


def change_member_role(*, actor, membership: Membership, role: str, request=None) -> Membership:
    organization = membership.organization
    actor_role = require_capability(actor, organization.id, "members.manage")
    if role not in Role.values:
        raise ValidationFailed("Unknown role.", details={"role": ["invalid"]})
    if membership.role == role:
        return membership
    touches_owner = Role.OWNER in (role, membership.role)
    if touches_owner and actor_role != Role.OWNER:
        raise PermissionDeniedError("Only owners can grant or remove the owner role.")
    if membership.user_id == organization.owner_id:
        raise PermissionDeniedError("The primary owner's role cannot be changed.")
    with transaction.atomic():
        if membership.role == Role.OWNER and _owner_count(organization) <= 1:
            raise Conflict("An organization must keep at least one owner.", code="LAST_OWNER")
        previous = membership.role
        membership.role = role
        membership.save(update_fields=["role", "updated_at"])
        record_audit(
            actor=actor,
            organization=organization,
            action="membership.role_changed",
            resource=membership,
            metadata={"user": membership.user.email, "from": previous, "to": role},
            request=request,
        )
    clear_role_cache(membership.user)
    return membership


def remove_member(*, actor, membership: Membership, request=None) -> None:
    organization = membership.organization
    is_self = membership.user_id == actor.id
    if not is_self:
        actor_role = require_capability(actor, organization.id, "members.manage")
        if membership.role == Role.OWNER and actor_role != Role.OWNER:
            raise PermissionDeniedError("Only owners can remove another owner.")
    if membership.user_id == organization.owner_id:
        raise PermissionDeniedError("The primary owner cannot be removed from the organization.")
    with transaction.atomic():
        if membership.role == Role.OWNER and _owner_count(organization) <= 1:
            raise Conflict("An organization must keep at least one owner.", code="LAST_OWNER")
        record_audit(
            actor=actor,
            organization=organization,
            action="membership.removed",
            resource=membership,
            metadata={"user": membership.user.email, "role": membership.role, "self": is_self},
            request=request,
        )
        membership.delete()
    clear_role_cache(membership.user)


# --------------------------------------------------------------------------------------
# Invitations
# --------------------------------------------------------------------------------------


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def invite_member(*, actor, organization: Organization, email: str, role: str, request=None) -> tuple[Invitation, str]:
    """Create an invitation and email it. Returns ``(invitation, accept_url)``."""
    actor_role = require_capability(actor, organization.id, "members.manage")
    email = email.strip().lower()
    if role == Role.OWNER and actor_role != Role.OWNER:
        raise PermissionDeniedError("Only owners can invite another owner.")
    if organization.memberships.filter(user__email__iexact=email).exists():
        raise Conflict("That person is already a member of this organization.", code="ALREADY_MEMBER")
    token = secrets.token_urlsafe(32)
    accept_url = f"{settings.SITE_URL}/invitations/accept/?token={token}"
    try:
        with transaction.atomic():
            invitation = Invitation.objects.create(
                organization=organization,
                email=email,
                role=role,
                token_hash=_hash_token(token),
                invited_by=actor,
                expires_at=timezone.now() + timedelta(days=settings.INVITATION_TTL_DAYS),
            )
            record_audit(
                actor=actor,
                organization=organization,
                action="invitation.created",
                resource=invitation,
                metadata={"email": email, "role": role},
                request=request,
            )
            from apps.notifications.tasks import send_email_message

            body = (
                f"{actor.get_full_name()} invited you to join {organization.name} on WatchGrid as {role}.\n\n"
                f"Accept the invitation here:\n{accept_url}\n\n"
                f"The invitation expires in {settings.INVITATION_TTL_DAYS} days."
            )
            transaction.on_commit(
                lambda: send_email_message.delay([email], f"You're invited to {organization.name} on WatchGrid", body)
            )
    except IntegrityError as exc:
        raise Conflict("A pending invitation already exists for that email.", code="INVITATION_PENDING") from exc
    return invitation, accept_url


def revoke_invitation(*, actor, invitation: Invitation, request=None) -> None:
    require_capability(actor, invitation.organization_id, "members.manage")
    if invitation.accepted_at or invitation.revoked_at:
        return
    invitation.revoked_at = timezone.now()
    invitation.save(update_fields=["revoked_at", "updated_at"])
    record_audit(
        actor=actor,
        organization=invitation.organization,
        action="invitation.revoked",
        resource=invitation,
        metadata={"email": invitation.email},
        request=request,
    )


def accept_invitation(*, user, token: str, request=None) -> Membership:
    invitation = Invitation.objects.select_related("organization").filter(token_hash=_hash_token(token or "")).first()
    if invitation is None or not invitation.is_pending or invitation.organization.archived_at is not None:
        raise ValidationFailed("This invitation is invalid or has expired.", code="INVALID_INVITATION")
    if invitation.email.lower() != user.email.lower():
        raise PermissionDeniedError(
            "This invitation was sent to a different email address.", code="INVITATION_EMAIL_MISMATCH"
        )
    with transaction.atomic():
        membership, created = Membership.objects.get_or_create(
            organization=invitation.organization, user=user, defaults={"role": invitation.role}
        )
        invitation.accepted_at = timezone.now()
        invitation.accepted_by = user
        invitation.save(update_fields=["accepted_at", "accepted_by", "updated_at"])
        if created:
            record_audit(
                actor=user,
                organization=invitation.organization,
                action="membership.joined",
                resource=membership,
                metadata={"role": membership.role, "via": "invitation"},
                request=request,
            )
        if user.email_verified_at is None:
            # Accepting a link delivered to this address proves ownership of it.
            user.email_verified_at = timezone.now()
            user.save(update_fields=["email_verified_at"])
    clear_role_cache(user)
    return membership
