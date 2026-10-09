"""Account workflows: registration, verification and password recovery."""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from common.exceptions import ValidationFailed

from .models import User

security_log = logging.getLogger("watchgrid.security")

_VERIFY_SALT = "watchgrid.accounts.verify-email"


def _queue_email(to: str, subject: str, body: str) -> None:
    from apps.notifications.tasks import send_email_message

    transaction.on_commit(lambda: send_email_message.delay([to], subject, body))


def register_user(*, email: str, password: str, full_name: str = "") -> User:
    with transaction.atomic():
        user = User.objects.create_user(email=email, password=password, full_name=full_name)
        send_verification_email(user)
    return user


def make_verification_token(user: User) -> str:
    return signing.dumps({"uid": str(user.pk), "email": user.email}, salt=_VERIFY_SALT)


def send_verification_email(user: User) -> None:
    token = make_verification_token(user)
    link = f"{settings.SITE_URL}/verify-email/?token={token}"
    _queue_email(
        user.email,
        "Verify your WatchGrid email address",
        f"Hello {user.get_short_name()},\n\nConfirm your email address by opening this link:\n{link}\n\n"
        "The link expires in 72 hours. If you did not create a WatchGrid account you can ignore this message.",
    )


def verify_email(token: str) -> User:
    try:
        data = signing.loads(token, salt=_VERIFY_SALT, max_age=settings.EMAIL_VERIFICATION_TIMEOUT)
    except signing.BadSignature as exc:
        raise ValidationFailed("The verification link is invalid or has expired.", code="INVALID_TOKEN") from exc
    user = User.objects.filter(pk=data.get("uid"), email=data.get("email"), is_active=True).first()
    if user is None:
        raise ValidationFailed("The verification link is invalid or has expired.", code="INVALID_TOKEN")
    if user.email_verified_at is None:
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email_verified_at"])
    return user


def request_password_reset(email: str) -> None:
    """Send a reset link when the account exists. Never reveals whether it does."""
    user = User.objects.filter(email__iexact=(email or "").strip(), is_active=True).first()
    if user is None or not user.has_usable_password():
        security_log.info("password_reset_unknown_account")
        return
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    link = f"{settings.SITE_URL}/reset-password/?uid={uid}&token={token}"
    _queue_email(
        user.email,
        "Reset your WatchGrid password",
        f"Hello {user.get_short_name()},\n\nA password reset was requested for your account. "
        f"Choose a new password here:\n{link}\n\nThe link expires in one hour and can be used once. "
        "If you did not request this, no action is needed.",
    )


def confirm_password_reset(*, uid: str, token: str, new_password: str) -> User:
    try:
        user = User.objects.get(pk=force_str(urlsafe_base64_decode(uid)), is_active=True)
    except (User.DoesNotExist, ValueError, TypeError, OverflowError, DjangoValidationError):
        user = None
    if user is None or not default_token_generator.check_token(user, token):
        raise ValidationFailed("The reset link is invalid or has expired.", code="INVALID_TOKEN")
    user.set_password(new_password)
    user.save(update_fields=["password"])
    security_log.info("password_reset_completed", extra={"user_id": str(user.pk)})
    return user
