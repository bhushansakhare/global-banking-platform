"""Permission catalogue and system role definitions (least privilege)."""
from __future__ import annotations

PERMISSIONS: dict[str, str] = {
    "dashboard:read": "View dashboard and analytics",
    "accounts:read": "View bank accounts, balances and institutions",
    "accounts:write": "Create, edit and connect bank accounts",
    "connections:manage": "Connect/disconnect providers and manage credentials",
    "statements:read": "View and download statements",
    "statements:write": "Upload and reprocess statements",
    "transactions:read": "View transactions",
    "transactions:write": "Categorise transactions and import book entries",
    "reconciliation:read": "View reconciliation results",
    "reconciliation:write": "Run reconciliation and record manual decisions",
    "automation:read": "View automation jobs and logs",
    "automation:manage": "Control automation, schedules and job retries",
    "reports:read": "View and download reports",
    "reports:write": "Generate reports",
    "notifications:read": "View notifications",
    "audit:read": "View audit logs",
    "settings:read": "View settings",
    "settings:manage": "Change organisation settings",
    "users:manage": "Manage users and roles",
}

_ALL = list(PERMISSIONS)
_READ = [p for p in _ALL if p.endswith(":read")]

SYSTEM_ROLES: dict[str, dict] = {
    "owner": {"name": "Owner", "permissions": _ALL},
    "admin": {"name": "Administrator", "permissions": [p for p in _ALL if p != "users:manage"] + ["users:manage"]},
    "analyst": {
        "name": "Finance Analyst",
        "permissions": _READ + ["statements:write", "transactions:write", "reconciliation:write", "reports:write",
                                "automation:manage"],
    },
    "viewer": {"name": "Viewer", "permissions": [p for p in _READ if p != "audit:read"]},
}
