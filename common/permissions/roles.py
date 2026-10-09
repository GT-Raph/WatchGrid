"""Permission matrix (Section 10.2) expressed as named capabilities.

"Limited" cells in the specification are resolved as follows (see docs/adr/0004):

* Create/edit projects — developers may edit existing project details but may not
  create or archive projects.
* Manage alert policies — developers may create and edit policies but may not delete.
* View audit log — developers see entries for project-level resources only
  (projects, monitors, incidents, alert policies, API keys, maintenance windows),
  not organization, membership or security entries.
"""

from __future__ import annotations

from common.constants import Role

OWNER, ADMIN, DEVELOPER, VIEWER = Role.OWNER, Role.ADMIN, Role.DEVELOPER, Role.VIEWER
ALL_ROLES = frozenset({OWNER, ADMIN, DEVELOPER, VIEWER})
MANAGERS = frozenset({OWNER, ADMIN})
OPERATORS = frozenset({OWNER, ADMIN, DEVELOPER})

CAPABILITIES: dict[str, frozenset[str]] = {
    "org.view": ALL_ROLES,
    "org.manage": MANAGERS,
    "org.archive": frozenset({OWNER}),
    "members.view": ALL_ROLES,
    "members.manage": MANAGERS,
    "project.view": ALL_ROLES,
    "project.create": MANAGERS,
    "project.edit": OPERATORS,
    "project.archive": MANAGERS,
    "monitor.view": ALL_ROLES,
    "monitor.manage": OPERATORS,
    "monitor.operate": OPERATORS,  # pause / resume / manual test
    "incident.view": ALL_ROLES,
    "incident.manage": OPERATORS,
    "errors.view": ALL_ROLES,
    "errors.manage": OPERATORS,
    "apikey.view": OPERATORS,
    "apikey.manage": OPERATORS,
    "alertpolicy.view": ALL_ROLES,
    "alertpolicy.manage": OPERATORS,
    "alertpolicy.delete": MANAGERS,
    "maintenance.view": ALL_ROLES,
    "maintenance.manage": OPERATORS,
    "audit.view": OPERATORS,
    "audit.view_all": MANAGERS,
}

PROJECT_LEVEL_AUDIT_RESOURCES = frozenset(
    {"project", "monitor", "incident", "alert_policy", "api_key", "maintenance_window", "error_group"}
)


def role_has(role: str | None, capability: str) -> bool:
    if role is None:
        return False
    try:
        return role in CAPABILITIES[capability]
    except KeyError as exc:  # pragma: no cover - programming error
        raise ValueError(f"Unknown capability: {capability}") from exc
