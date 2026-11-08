import uuid
import pytest
from unittest.mock import AsyncMock, patch
from fastapi import Request
from app.core.errors import BadRequestException, ForbiddenException
from app.core.permissions import (
    ROLE_ADMIN,
    ROLE_EMPLOYEE,
    ROLE_OWNER,
    ROLE_PERMISSIONS,
    AuthContext,
    get_auth_context,
)
from app.core.auth import UserTokenPayload


def test_permission_matrix_codes():
    owner_perms = ROLE_PERMISSIONS[ROLE_OWNER]
    admin_perms = ROLE_PERMISSIONS[ROLE_ADMIN]
    emp_perms = ROLE_PERMISSIONS[ROLE_EMPLOYEE]

    # Owner has all permissions
    assert "customers.read" in owner_perms
    assert "customers.write" in owner_perms
    assert "expenses.void" in owner_perms
    assert "suppliers.write" in owner_perms

    # Admin has almost all, but NOT business.delete or payments.configure
    assert "business.delete" not in admin_perms
    assert "payments.configure" not in admin_perms
    assert "expenses.void" in admin_perms

    # Employee has read & own write, but cannot void or manage suppliers
    assert "expenses.record" in emp_perms
    assert "expenses.view" in emp_perms
    assert "expenses.void" not in emp_perms
    assert "suppliers.write" not in emp_perms
    assert "categories.write" not in emp_perms


@pytest.mark.asyncio
async def test_get_auth_context_single_business_default():
    user_id = str(uuid.uuid4())
    bus_id = str(uuid.uuid4())

    token = UserTokenPayload(
        sub=user_id,
        memberships={bus_id: "OWNER"},
    )

    request = AsyncMock(spec=Request)

    with patch("app.core.permissions.get_current_user_token", new=AsyncMock(return_value=token)):
        ctx = await get_auth_context(request=request, x_business_id=None)
        assert ctx.user_id == uuid.UUID(user_id)
        assert ctx.business_id == uuid.UUID(bus_id)
        assert ctx.role == "OWNER"
        assert ctx.is_owner is True
        assert ctx.has_permission("customers.write") is True


@pytest.mark.asyncio
async def test_get_auth_context_multiple_businesses_missing_header():
    user_id = str(uuid.uuid4())
    bus_1 = str(uuid.uuid4())
    bus_2 = str(uuid.uuid4())

    token = UserTokenPayload(
        sub=user_id,
        memberships={bus_1: "OWNER", bus_2: "EMPLOYEE"},
    )

    request = AsyncMock(spec=Request)

    with patch("app.core.permissions.get_current_user_token", new=AsyncMock(return_value=token)):
        with pytest.raises(BadRequestException) as exc_info:
            await get_auth_context(request=request, x_business_id=None)
        assert exc_info.value.code == "BUSINESS_ID_REQUIRED"


@pytest.mark.asyncio
async def test_get_auth_context_specified_business_not_in_memberships():
    user_id = str(uuid.uuid4())
    bus_1 = str(uuid.uuid4())
    foreign_bus = str(uuid.uuid4())

    token = UserTokenPayload(
        sub=user_id,
        memberships={bus_1: "ADMIN"},
    )

    request = AsyncMock(spec=Request)

    with patch("app.core.permissions.get_current_user_token", new=AsyncMock(return_value=token)):
        with pytest.raises(ForbiddenException):
            await get_auth_context(request=request, x_business_id=foreign_bus)
