"""Alert policy form: email routing and webhooks as native controls."""

from django import forms
from django.core.exceptions import ValidationError

from common.constants import Role
from common.utilities.forms import SerializerForm

from .models import EventType


class AlertPolicyForm(SerializerForm):
    """Present email routing and webhooks as native controls, keeping stored URLs secret."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["event_types"] = forms.MultipleChoiceField(
            choices=EventType.choices, widget=forms.CheckboxSelectMultiple, label="Alert events"
        )
        self.fields.pop("channels", None)
        self.previous_channels = self.initial.pop("channels", [])
        email_channels = [c for c in self.previous_channels if c.get("type") == "email"]
        self.fields["recipients"] = forms.CharField(
            required=False,
            label="Email recipients",
            widget=forms.Textarea(attrs={"rows": 2}),
            help_text="Separate email addresses with commas.",
        )
        self.initial["recipients"] = ", ".join(sorted({v for c in email_channels for v in c.get("to", [])}))
        self.fields["recipient_roles"] = forms.MultipleChoiceField(
            required=False,
            label="Notify members with these roles",
            choices=Role.choices,
            widget=forms.CheckboxSelectMultiple,
        )
        self.initial["recipient_roles"] = sorted({v for c in email_channels for v in c.get("to_roles", [])})
        self.fields["webhook_url"] = forms.URLField(
            required=False,
            assume_scheme="https",
            label="Add a webhook URL",
            widget=forms.PasswordInput(render_value=False),
            help_text="An HTTPS destination. Stored URLs are kept private.",
        )
        self.webhooks = [c for c in self.previous_channels if c.get("type") == "webhook"]
        if self.webhooks:
            self.fields["keep_webhooks"] = forms.BooleanField(
                required=False, label="Keep existing webhook destinations"
            )
            self.initial["keep_webhooks"] = True

    def prepare_payload(self, payload, cleaned):
        recipients = [
            value.strip() for value in cleaned.get("recipients", "").replace("\n", ",").split(",") if value.strip()
        ]
        email_field = forms.EmailField()
        for address in recipients:
            try:
                email_field.clean(address)
            except ValidationError as exc:
                self.add_error("recipients", exc)
        channels = []
        roles = cleaned.get("recipient_roles", [])
        if recipients or roles:
            channels.append({"type": "email", "to": recipients, "to_roles": roles})
        if cleaned.get("keep_webhooks"):
            channels.extend({"type": "webhook"} for _ in self.webhooks)
        if cleaned.get("webhook_url"):
            channels.append({"type": "webhook", "url": cleaned["webhook_url"]})
        payload["channels"] = channels
        return payload
