"""HTML forms that reuse the REST serializers, so both interfaces validate identically
(including URL safety checks and secret redaction)."""

from django import forms
from django.core.exceptions import FieldDoesNotExist, ValidationError
from rest_framework import serializers

from common.exceptions import DomainError


def error_text(value):
    if isinstance(value, dict):
        return "; ".join(f"{key}: {error_text(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return "; ".join(error_text(item) for item in value)
    return str(value)


class SerializerForm(forms.Form):
    """Adapt an explicit set of serializer fields to accessible native HTML inputs."""

    def __init__(
        self,
        serializer_class,
        data=None,
        *,
        instance=None,
        request=None,
        fields=None,
        initial=None,
        choices=None,
        hidden=(),
        partial=False,
    ):
        self.serializer_class = serializer_class
        self.instance = instance
        self.request = request
        self.partial = partial
        serializer = serializer_class(instance=instance, context={"request": request})
        defaults = dict(serializer.data) if instance is not None else {}
        defaults.update(initial or {})
        super().__init__(data=data, initial=defaults)
        for name, field in serializer.fields.items():
            if field.read_only or (fields is not None and name not in fields):
                continue
            required = field.required and not partial
            opts = {
                "label": field.label or name.replace("_", " ").capitalize(),
                "required": required,
                "help_text": field.help_text or "",
            }
            if choices and name in choices:
                self.fields[name] = forms.ChoiceField(choices=choices[name], **opts)
            elif isinstance(field, serializers.ChoiceField):
                self.fields[name] = forms.ChoiceField(choices=list(field.choices.items()), **opts)
            elif isinstance(field, serializers.BooleanField):
                self.fields[name] = forms.BooleanField(required=False, label=opts["label"])
            elif isinstance(field, serializers.IntegerField):
                self.fields[name] = forms.IntegerField(min_value=field.min_value, max_value=field.max_value, **opts)
            elif isinstance(field, serializers.EmailField):
                self.fields[name] = forms.EmailField(**opts)
            elif isinstance(field, serializers.DateTimeField):
                self.fields[name] = forms.DateTimeField(
                    widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}), **opts
                )
            elif isinstance(
                field, (serializers.DictField, serializers.ListField, serializers.ListSerializer, serializers.JSONField)
            ):
                self.fields[name] = forms.JSONField(widget=forms.Textarea(attrs={"rows": 4}), **opts)
            else:
                widget = forms.TextInput()
                if "password" in name:
                    widget = forms.PasswordInput(
                        attrs={
                            "autocomplete": "current-password"
                            if name in ("password", "current_password")
                            else "new-password"
                        }
                    )
                elif name in ("description", "body", "message", "note", "assertion_text"):
                    widget = forms.Textarea(attrs={"rows": 3})
                self.fields[name] = forms.CharField(
                    widget=widget, max_length=getattr(field, "max_length", None), strip="password" not in name, **opts
                )
            if name in hidden:
                self.fields[name].widget = forms.HiddenInput()
            if name not in self.initial:
                if field.default is not serializers.empty:
                    self.initial[name] = field.get_default()
                elif hasattr(serializer, "Meta") and hasattr(serializer.Meta, "model"):
                    try:
                        model_field = serializer.Meta.model._meta.get_field(name)
                        if model_field.has_default():
                            self.initial[name] = model_field.get_default()
                    except FieldDoesNotExist:
                        self.initial.setdefault(name, None)
            self.fields[name].widget.attrs.setdefault("class", "input")
        if "headers" in self.fields:
            self.fields[
                "headers"
            ].help_text = 'JSON object, e.g. {"Accept": "application/json"}. Redacted values retain stored secrets.'

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        payload = {}
        for name, value in cleaned.items():
            if name not in self.serializer_class().fields:
                continue
            field = self.serializer_class().fields[name]
            if value in (None, "") and not field.required:
                if field.allow_null:
                    payload[name] = None
                elif getattr(field, "allow_blank", False):
                    payload[name] = ""
                elif isinstance(field, serializers.DictField):
                    payload[name] = {}
                continue
            payload[name] = value
        payload = self.prepare_payload(payload, cleaned)
        self.serializer = self.serializer_class(
            instance=self.instance, data=payload, partial=self.partial, context={"request": self.request}
        )
        try:
            valid = self.serializer.is_valid()
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc
        if not valid:
            self.attach_errors(self.serializer.errors)
        else:
            self.validated_data = self.serializer.validated_data
        return cleaned

    def prepare_payload(self, payload, cleaned):
        return payload

    def attach_errors(self, errors):
        for name, value in errors.items():
            self.add_error(name if name in self.fields else None, error_text(value))

    def service_error(self, exc):
        if isinstance(exc, DomainError):
            self.attach_errors(exc.details) if exc.details else self.add_error(None, exc.message)
        else:
            self.add_error(None, error_text(exc.detail))


class NoteForm(forms.Form):
    message = forms.CharField(max_length=5000, widget=forms.Textarea(attrs={"rows": 3, "class": "input"}))


class ConfirmForm(forms.Form):
    confirm = forms.BooleanField(label="I understand and want to continue")
