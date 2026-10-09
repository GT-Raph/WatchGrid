"""Notification provider abstraction (Section 17.1).

Incident logic never talks to a delivery channel directly; it creates ledger rows and
the provider registry turns them into email, webhook or — later — SMS, WhatsApp,
Slack, Teams or Discord deliveries without changes to monitoring code.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

from common.security.network import TargetBlockedError
from common.security.safe_http import TRANSPORT_EXCEPTIONS, DeadlineExceeded, RedirectError, perform_request
from common.security.urls import UnsafeURLError


class DeliveryError(Exception):
    """Base delivery failure."""

    retryable = True


class TransientDeliveryError(DeliveryError):
    retryable = True


class PermanentDeliveryError(DeliveryError):
    retryable = False


class NotificationProvider(ABC):
    channel: str

    @abstractmethod
    def send(self, *, recipient: str, target: dict, payload: dict) -> None:
        """Deliver ``payload``. Raise ``DeliveryError`` subclasses on failure."""


class EmailProvider(NotificationProvider):
    channel = "email"

    def send(self, *, recipient: str, target: dict, payload: dict) -> None:
        message = EmailMultiAlternatives(
            subject=payload.get("subject", "WatchGrid notification")[:250],
            body=payload.get("text", ""),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient],
        )
        try:
            message.send(fail_silently=False)
        except OSError as exc:  # smtplib exceptions subclass OSError
            raise TransientDeliveryError(f"Email delivery failed: {exc.__class__.__name__}") from exc


class WebhookProvider(NotificationProvider):
    """Generic JSON webhook (Slack-compatible ``text`` field), sent via the SSRF-safe client."""

    channel = "webhook"

    def send(self, *, recipient: str, target: dict, payload: dict) -> None:
        url = target.get("url")
        if not url:
            raise PermanentDeliveryError("Webhook target is missing.")
        body = json.dumps(payload.get("webhook") or {"text": payload.get("text", "")}).encode()
        try:
            response = perform_request(
                url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
                timeout_seconds=settings.WEBHOOK_TIMEOUT_SECONDS,
                follow_redirects=False,
                max_response_bytes=8192,
                user_agent=settings.MONITOR_USER_AGENT,
            )
        except (TargetBlockedError, UnsafeURLError, RedirectError) as exc:
            raise PermanentDeliveryError("Webhook destination is not allowed by network policy.") from exc
        except (DeadlineExceeded, *TRANSPORT_EXCEPTIONS) as exc:
            raise TransientDeliveryError(f"Webhook request failed: {exc.__class__.__name__}") from exc
        if 200 <= response.status < 300:
            return
        if response.status == 429 or response.status >= 500:
            raise TransientDeliveryError(f"Webhook returned HTTP {response.status}.")
        raise PermanentDeliveryError(f"Webhook returned HTTP {response.status}.")


PROVIDERS: dict[str, NotificationProvider] = {
    EmailProvider.channel: EmailProvider(),
    WebhookProvider.channel: WebhookProvider(),
}


def get_provider(channel: str) -> NotificationProvider:
    try:
        return PROVIDERS[channel]
    except KeyError as exc:
        raise PermanentDeliveryError(f"No provider registered for channel '{channel}'.") from exc
