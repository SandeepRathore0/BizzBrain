"""Unit tests for the RBAC permissions policy matrix."""

from app.core.permissions import (
    Permission,
    Role,
    can_manage_member,
    has_permission,
)


def test_owner_permissions():
    # Owner has all permissions
    for perm in Permission:
        assert has_permission(Role.OWNER.value, perm.value) is True


def test_admin_permissions():
    # Admin has all permissions EXCEPT business.delete and payments.configure
    assert has_permission(Role.ADMIN.value, Permission.BUSINESS_DELETE.value) is False
    assert has_permission(Role.ADMIN.value, Permission.PAYMENTS_CONFIGURE.value) is False

    assert has_permission(Role.ADMIN.value, Permission.MEMBERS_MANAGE.value) is True
    assert has_permission(Role.ADMIN.value, Permission.BUSINESS_UPDATE.value) is True
    assert has_permission(Role.ADMIN.value, Permission.INVOICES_ISSUE.value) is True
    assert has_permission(Role.ADMIN.value, Permission.INVOICES_VOID.value) is True
    assert has_permission(Role.ADMIN.value, Permission.EXPENSES_VOID.value) is True


def test_employee_permissions():
    # Employee has limited permissions
    assert has_permission(Role.EMPLOYEE.value, Permission.DASHBOARD_VIEW.value) is True
    assert has_permission(Role.EMPLOYEE.value, Permission.CUSTOMERS_READ.value) is True
    assert has_permission(Role.EMPLOYEE.value, Permission.CUSTOMERS_WRITE.value) is True
    assert has_permission(Role.EMPLOYEE.value, Permission.INVOICES_CREATE.value) is True
    assert has_permission(Role.EMPLOYEE.value, Permission.INVOICES_EDIT_DRAFT.value) is True
    assert has_permission(Role.EMPLOYEE.value, Permission.EXPENSES_RECORD.value) is True
    assert has_permission(Role.EMPLOYEE.value, Permission.EXPENSES_VIEW.value) is True
    assert has_permission(Role.EMPLOYEE.value, Permission.PAYMENTS_VIEW.value) is True

    # Employee cannot issue/void invoices or void expenses
    assert has_permission(Role.EMPLOYEE.value, Permission.INVOICES_ISSUE.value) is False
    assert has_permission(Role.EMPLOYEE.value, Permission.INVOICES_VOID.value) is False
    assert has_permission(Role.EMPLOYEE.value, Permission.EXPENSES_VOID.value) is False
    assert has_permission(Role.EMPLOYEE.value, Permission.MEMBERS_MANAGE.value) is False
    assert has_permission(Role.EMPLOYEE.value, Permission.BUSINESS_UPDATE.value) is False
    assert has_permission(Role.EMPLOYEE.value, Permission.BUSINESS_DELETE.value) is False


def test_member_management_hierarchy():
    # Owner can manage anyone and grant any role
    assert can_manage_member(Role.OWNER.value, Role.OWNER.value, Role.ADMIN.value) is True
    assert can_manage_member(Role.OWNER.value, Role.EMPLOYEE.value, Role.OWNER.value) is True

    # Admin cannot modify or remove an Owner
    assert can_manage_member(Role.ADMIN.value, Role.OWNER.value, None) is False
    assert can_manage_member(Role.ADMIN.value, Role.OWNER.value, Role.EMPLOYEE.value) is False

    # Admin cannot promote anyone to Owner
    assert can_manage_member(Role.ADMIN.value, Role.EMPLOYEE.value, Role.OWNER.value) is False
    assert can_manage_member(Role.ADMIN.value, Role.ADMIN.value, Role.OWNER.value) is False

    # Admin CAN manage Employees and Admins as long as not promoting to Owner
    assert can_manage_member(Role.ADMIN.value, Role.EMPLOYEE.value, Role.ADMIN.value) is True
    assert can_manage_member(Role.ADMIN.value, Role.ADMIN.value, Role.EMPLOYEE.value) is True

    # Employee cannot manage anyone
    assert can_manage_member(Role.EMPLOYEE.value, Role.EMPLOYEE.value) is False
