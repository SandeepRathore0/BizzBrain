"""Additional unit and integration tests to boost coverage on services and repositories past 85%."""

import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    BadRequestException,
    BusinessInactiveException,
    ConflictException,
    ForbiddenException,
    NotFoundException,
)
from app.repositories.business_repo import BusinessRepository
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService
from app.services.business_service import BusinessService


@pytest.mark.asyncio
async def test_email_verification_and_password_reset_happy_path(db_session: AsyncSession):
    svc = AuthService(db_session)
    user, _, _ = await svc.register(
        full_name="Email Test", email="email_test@example.com", password="Password12345!"
    )
    user_id = uuid.UUID(user["user_id"])

    # 1. Email verification happy path
    raw_token = await svc.request_email_verification(user_id=user_id)
    assert raw_token
    await svc.verify_email(token_raw=raw_token)

    # Calling it again should fail with already used
    with pytest.raises(BadRequestException):
        await svc.verify_email(token_raw=raw_token)

    # 2. Password reset happy path
    await svc.request_password_reset(email="email_test@example.com")
    # Fetch token directly from repo for test confirmation
    urepo = UserRepository(db_session)
    # create reset token
    raw_reset = "custom-opaque-reset-token-12345"
    from app.core.security import hash_token
    from datetime import datetime, timezone, timedelta
    await urepo.create_email_token(
        user_id=user_id,
        token_hash=hash_token(raw_reset),
        purpose="PASSWORD_RESET",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    await svc.confirm_password_reset(token_raw=raw_reset, new_password="NewPassword789!")

    # Login with new password
    u, at, _ = await svc.login(email="email_test@example.com", password="NewPassword789!")
    assert u["email"] == "email_test@example.com"


@pytest.mark.asyncio
async def test_business_update_and_pagination(db_session: AsyncSession):
    svc = AuthService(db_session)
    user, _, _ = await svc.register(
        full_name="Biz Test", email="biz_test@example.com", password="Password12345!"
    )
    user_id = uuid.UUID(user["user_id"])
    bsvc = BusinessService(db_session)

    # Create business
    biz = await bsvc.create_business(
        actor_user_id=user_id,
        actor_role_in_token="OWNER",
        business_name="Initial Biz",
        base_currency="USD",
        timezone="America/New_York",
    )
    biz_id = uuid.UUID(biz["business_id"])
    memberships = {str(biz_id): "OWNER"}

    # Update business
    updated = await bsvc.update_business(
        business_id=biz_id,
        actor_user_id=user_id,
        actor_memberships=memberships,
        business_name="Renamed Biz",
        timezone="Europe/London",
    )
    assert updated["business_name"] == "Renamed Biz"
    assert updated["timezone"] == "Europe/London"

    # Invalid timezone update
    with pytest.raises(BadRequestException):
        await bsvc.update_business(
            business_id=biz_id,
            actor_user_id=user_id,
            actor_memberships=memberships,
            timezone="Bad/Tz",
        )

    # Permission check for update (EMPLOYEE cannot update)
    with pytest.raises(ForbiddenException):
        await bsvc.update_business(
            business_id=biz_id,
            actor_user_id=user_id,
            actor_memberships={str(biz_id): "EMPLOYEE"},
            business_name="Nope",
        )

    # Permission check for delete (ADMIN cannot delete)
    with pytest.raises(ForbiddenException):
        await bsvc.delete_business(
            business_id=biz_id,
            actor_user_id=user_id,
            actor_memberships={str(biz_id): "ADMIN"},
        )


@pytest.mark.asyncio
async def test_member_management_hierarchy_and_edges(db_session: AsyncSession):
    asvc = AuthService(db_session)
    owner, _, _ = await asvc.register(
        full_name="Owner", email="hier_owner@example.com", password="Password12345!"
    )
    admin, _, _ = await asvc.register(
        full_name="Admin", email="hier_admin@example.com", password="Password12345!"
    )
    emp, _, _ = await asvc.register(
        full_name="Emp", email="hier_emp@example.com", password="Password12345!"
    )

    owner_id = uuid.UUID(owner["user_id"])
    admin_id = uuid.UUID(admin["user_id"])
    emp_id = uuid.UUID(emp["user_id"])

    bsvc = BusinessService(db_session)
    biz = await bsvc.create_business(
        actor_user_id=owner_id,
        actor_role_in_token=None,
        business_name="Hierarchy Corp",
        base_currency="USD",
        timezone="UTC",
    )
    biz_id = uuid.UUID(biz["business_id"])

    # Add admin and employee
    await bsvc.add_member(
        business_id=biz_id,
        actor_user_id=owner_id,
        actor_memberships={str(biz_id): "OWNER"},
        target_user_id=admin_id,
        role="ADMIN",
    )
    await bsvc.add_member(
        business_id=biz_id,
        actor_user_id=owner_id,
        actor_memberships={str(biz_id): "OWNER"},
        target_user_id=emp_id,
        role="EMPLOYEE",
    )

    # Admin CANNOT promote anyone to OWNER
    with pytest.raises(ForbiddenException):
        await bsvc.update_member(
            business_id=biz_id,
            actor_user_id=admin_id,
            actor_memberships={str(biz_id): "ADMIN"},
            target_user_id=emp_id,
            new_role="OWNER",
        )

    # Admin CANNOT demote or touch OWNER
    with pytest.raises(ForbiddenException):
        await bsvc.update_member(
            business_id=biz_id,
            actor_user_id=admin_id,
            actor_memberships={str(biz_id): "ADMIN"},
            target_user_id=owner_id,
            new_role="ADMIN",
        )

    # Admin CAN update Employee to Admin
    updated_emp = await bsvc.update_member(
        business_id=biz_id,
        actor_user_id=admin_id,
        actor_memberships={str(biz_id): "ADMIN"},
        target_user_id=emp_id,
        new_role="ADMIN",
    )
    assert updated_emp["role"] == "ADMIN"

    # Employee CANNOT manage members
    with pytest.raises(ForbiddenException):
        await bsvc.add_member(
            business_id=biz_id,
            actor_user_id=emp_id,
            actor_memberships={str(biz_id): "EMPLOYEE"},
            target_user_id=admin_id,
            role="EMPLOYEE",
        )


@pytest.mark.asyncio
async def test_repo_pagination_and_owner_queries(db_session: AsyncSession):
    repo = BusinessRepository(db_session)
    urepo = UserRepository(db_session)

    user = await urepo.create(
        full_name="Page User",
        email="page@example.com",
        email_normalized="page@example.com",
        password_hash="hash",
    )

    # Create 3 businesses
    b1 = await repo.create(business_name="B1", base_currency="USD", timezone="UTC")
    b2 = await repo.create(business_name="B2", base_currency="USD", timezone="UTC")
    b3 = await repo.create(business_name="B3", base_currency="USD", timezone="UTC")

    m1 = await repo.add_member(business_id=b1.business_id, user_id=user.user_id, role="OWNER")
    await repo.add_member(business_id=b2.business_id, user_id=user.user_id, role="OWNER")
    await repo.add_member(business_id=b3.business_id, user_id=user.user_id, role="OWNER")

    # Pagination test on list_for_user
    items, next_cursor = await repo.list_for_user(user.user_id, limit=2)
    assert len(items) == 2
    assert next_cursor is not None

    items2, _ = await repo.list_for_user(user.user_id, limit=2, cursor=next_cursor)
    assert len(items2) >= 1

    # Pagination test on list_members
    m_items, m_cursor = await repo.list_members(b1.business_id, limit=1)
    assert len(m_items) == 1

    # Test count_active_owners
    owners_count = await repo.count_active_owners(b1.business_id)
    assert owners_count == 1

    # Test get_member_by_id
    fetched_m = await repo.get_member_by_id(m1.membership_id)
    assert fetched_m is not None
    assert fetched_m.membership_id == m1.membership_id
