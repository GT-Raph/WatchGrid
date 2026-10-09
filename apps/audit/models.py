from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """Append-only record of security-sensitive changes (FR-AUD-001)."""

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_logs"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_logs"
    )
    actor_email = models.CharField(max_length=254, blank=True)
    action = models.CharField(max_length=64)
    resource_type = models.CharField(max_length=40, blank=True)
    resource_id = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=256, blank=True)
    request_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["organization", "-created_at"], name="audit_org_created_idx"),
            models.Index(fields=["resource_type", "resource_id"], name="audit_resource_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.action} by {self.actor_email or 'system'}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not kwargs.pop("force_audit_update", False):
            raise RuntimeError("Audit log entries are append-only.")
        super().save(*args, **kwargs)
