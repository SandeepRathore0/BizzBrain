from typing import Set

# BizBrain Role Definitions
ROLE_OWNER = "OWNER"
ROLE_ADMIN = "ADMIN"
ROLE_EMPLOYEE = "EMPLOYEE"

VALID_ROLES: Set[str] = {ROLE_OWNER, ROLE_ADMIN, ROLE_EMPLOYEE}

# Permissions mapping
ROLE_PERMISSIONS: dict[str, Set[str]] = {
    ROLE_OWNER: {"payments.view", "payments.create", "payments.capture"},
    ROLE_ADMIN: {"payments.view", "payments.create", "payments.capture"},
    ROLE_EMPLOYEE: {"payments.view", "payments.create", "payments.capture"},
}


def has_permission(role: str, permission: str) -> bool:
    perms = ROLE_PERMISSIONS.get(role.upper(), set())
    return permission in perms
