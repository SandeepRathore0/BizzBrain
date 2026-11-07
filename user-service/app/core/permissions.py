"""Permission policy and Role-Based Access Control (RBAC) definition."""

from enum import Enum
from typing import Dict, FrozenSet, Set


class Role(str, Enum):
    """Membership roles within a business."""
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    EMPLOYEE = "EMPLOYEE"


class Permission(str, Enum):
    """System-wide permission codes mapped to roles."""
    # User Service permissions
    MEMBERS_MANAGE = "members.manage"
    BUSINESS_UPDATE = "business.update"
    BUSINESS_DELETE = "business.delete"

    # Financial Data Service permissions
    CUSTOMERS_READ = "customers.read"
    CUSTOMERS_WRITE = "customers.write"
    EXPENSES_RECORD = "expenses.record"
    EXPENSES_VIEW = "expenses.view"
    EXPENSES_VOID = "expenses.void"

    # Invoice Service permissions
    INVOICES_CREATE = "invoices.create"
    INVOICES_EDIT_DRAFT = "invoices.edit_draft"
    INVOICES_ISSUE = "invoices.issue"
    INVOICES_VOID = "invoices.void"

    # Payment Service & Dashboard permissions
    DASHBOARD_VIEW = "dashboard.view"
    PAYMENTS_VIEW = "payments.view"
    PAYMENTS_CONFIGURE = "payments.configure"


ALL_PERMISSIONS: FrozenSet[str] = frozenset(p.value for p in Permission)

# Explicit permission set for Employee
EMPLOYEE_PERMISSIONS: FrozenSet[str] = frozenset({
    Permission.DASHBOARD_VIEW.value,
    Permission.CUSTOMERS_READ.value,
    Permission.CUSTOMERS_WRITE.value,
    Permission.INVOICES_CREATE.value,
    Permission.INVOICES_EDIT_DRAFT.value,
    Permission.EXPENSES_RECORD.value,
    Permission.EXPENSES_VIEW.value,
    Permission.PAYMENTS_VIEW.value,
})

# Admin: everything except business.delete and payments.configure
ADMIN_PERMISSIONS: FrozenSet[str] = frozenset(
    ALL_PERMISSIONS - {
        Permission.BUSINESS_DELETE.value,
        Permission.PAYMENTS_CONFIGURE.value,
    }
)

# Owner: everything
OWNER_PERMISSIONS: FrozenSet[str] = ALL_PERMISSIONS

ROLE_PERMISSIONS: Dict[str, FrozenSet[str]] = {
    Role.OWNER.value: OWNER_PERMISSIONS,
    Role.ADMIN.value: ADMIN_PERMISSIONS,
    Role.EMPLOYEE.value: EMPLOYEE_PERMISSIONS,
}


def has_permission(role: str, permission: str) -> bool:
    """Checks whether a role possesses a specific permission code."""
    allowed = ROLE_PERMISSIONS.get(role, frozenset())
    return permission in allowed


def can_manage_member(actor_role: str, target_role: str, new_role: str | None = None) -> bool:
    """
    Enforces member management hierarchy:
    - Owner can manage any member, and can grant/revoke any role.
    - Admin can manage Admins and Employees, but CANNOT modify or remove an Owner,
      and CANNOT promote anyone to Owner.
    - Employee cannot manage members.
    """
    if actor_role == Role.OWNER.value:
        return True

    if actor_role == Role.ADMIN.value:
        # Admin cannot touch an existing Owner
        if target_role == Role.OWNER.value:
            return False
        # Admin cannot grant the OWNER role
        if new_role == Role.OWNER.value:
            return False
        return True

    return False
