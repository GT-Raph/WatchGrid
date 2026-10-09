"""Project workflows (FR-PROJ-001, BR-009 soft-delete)."""

from __future__ import annotations

import secrets

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.audit.services import record_audit
from common.exceptions import Conflict
from common.permissions.tenancy import require_capability

from .models import Project

EDITABLE_FIELDS = ("name", "description", "environment")


def _unique_slug(organization, base: str, exclude_pk=None) -> str:
    root = slugify(base)[:60] or "project"
    slug = root
    qs = Project.objects.filter(organization=organization)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    while qs.filter(slug=slug).exists():
        slug = f"{root}-{secrets.token_hex(2)}"
    return slug


def create_project(*, actor, organization, data: dict, request=None) -> Project:
    require_capability(actor, organization.id, "project.create")
    from apps.notifications.services import create_default_alert_policy

    with transaction.atomic():
        slug = data.get("slug")
        if slug:
            slug = slugify(slug)[:80]
            if Project.objects.filter(organization=organization, slug=slug).exists():
                raise Conflict("A project with that slug already exists.", code="SLUG_TAKEN")
        project = Project.objects.create(
            organization=organization,
            name=data["name"].strip(),
            slug=slug or _unique_slug(organization, data["name"]),
            description=data.get("description", ""),
            environment=data.get("environment") or Project._meta.get_field("environment").default,
            created_by=actor,
        )
        create_default_alert_policy(project)
        record_audit(
            actor=actor,
            organization=organization,
            action="project.created",
            resource=project,
            metadata={"name": project.name, "environment": project.environment},
            request=request,
        )
    return project


def update_project(*, actor, project: Project, data: dict, request=None) -> Project:
    require_capability(actor, project.organization_id, "project.edit")
    changes = {}
    for field in EDITABLE_FIELDS:
        if field in data and getattr(project, field) != data[field]:
            changes[field] = [getattr(project, field), data[field]]
            setattr(project, field, data[field])
    if changes:
        project.save(update_fields=[*changes.keys(), "updated_at"])
        record_audit(
            actor=actor,
            organization=project.organization,
            action="project.updated",
            resource=project,
            metadata={"changes": changes},
            request=request,
        )
    return project


def archive_project(*, actor, project: Project, request=None) -> Project:
    """Soft-delete: history is retained, monitoring stops (BR-009, BR-005)."""
    require_capability(actor, project.organization_id, "project.archive")
    if project.status == Project.Status.ARCHIVED:
        return project
    from apps.monitors.models import Monitor

    with transaction.atomic():
        project.status = Project.Status.ARCHIVED
        project.archived_at = timezone.now()
        project.save(update_fields=["status", "archived_at", "updated_at"])
        Monitor.objects.filter(project=project).update(is_active=False, status=Monitor.Status.PAUSED)
        record_audit(
            actor=actor,
            organization=project.organization,
            action="project.archived",
            resource=project,
            request=request,
        )
    return project


def restore_project(*, actor, project: Project, request=None) -> Project:
    require_capability(actor, project.organization_id, "project.archive")
    if project.status == Project.Status.ACTIVE:
        return project
    project.status = Project.Status.ACTIVE
    project.archived_at = None
    project.save(update_fields=["status", "archived_at", "updated_at"])
    record_audit(
        actor=actor,
        organization=project.organization,
        action="project.restored",
        resource=project,
        request=request,
    )
    return project
