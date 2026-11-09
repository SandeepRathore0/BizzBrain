from uuid import uuid4
import pytest
from app.core.auth import UserContext
from app.core.errors import ForbiddenException
from app.core.permissions import has_permission, require_permission


def test_permission_policy_owner():
    for perm in [
        "dashboard.view",
        "invoices.create",
        "invoices.edit_draft",
        "invoices.issue",
        "invoices.void",
        "invoices.view",
        "settings.manage",
        "reconciliation.manage",
    ]:
        assert has_permission("OWNER", perm) is True


def test_permission_policy_admin():
    for perm in [
        "dashboard.view",
        "invoices.create",
        "invoices.edit_draft",
        "invoices.issue",
        "invoices.void",
        "invoices.view",
        "settings.manage",
        "reconciliation.manage",
    ]:
        assert has_permission("ADMIN", perm) is True


def test_permission_policy_employee():
    assert has_permission("EMPLOYEE", "invoices.create") is True
    assert has_permission("EMPLOYEE", "invoices.edit_draft") is True
    assert has_permission("EMPLOYEE", "invoices.view") is True

    # Denied sensitive and management permissions
    assert has_permission("EMPLOYEE", "invoices.issue") is False
    assert has_permission("EMPLOYEE", "invoices.void") is False
    assert has_permission("EMPLOYEE", "settings.manage") is False
    assert has_permission("EMPLOYEE", "reconciliation.manage") is False


@pytest.mark.asyncio
async def test_require_permission_dependency():
    owner = UserContext(user_id=uuid4(), business_id=uuid4(), role="OWNER", memberships={})
    dep = require_permission("invoices.issue")
    result = await dep(user=owner)
    assert result == owner

    employee = UserContext(user_id=uuid4(), business_id=uuid4(), role="EMPLOYEE", memberships={})
    with pytest.raises(ForbiddenException, match="lacks required permission"):
        await dep(user=employee)
