"""Unit tests for the BusinessService business logic."""

import pytest
import uuid
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.auth_service import AuthService
from app.services.business_service import BusinessService
from app.core.errors import (
    BadRequestException,
    ConflictException,
    ForbiddenException,
    LastOwnerException,
    NotFoundException,
)


async def _register(db: AsyncSession, email: str, name: str = "User") -> tuple[dict, str, str]:
    svc = AuthService(db)
    return await svc.register(full_name=name, email=email, password="ValidPass12345!")


@pytest.mark.asyncio
async def test_create_business_owner_becomes_owner(db_session: AsyncSession):
    user, _, _ = await _register(db_session, "owner@biz.com", "Owner")
    bsvc = BusinessService(db_session)
    biz = await bsvc.create_business(
        actor_user_id=uuid.UUID(user["user_id"]),
        actor_role_in_token=None,
        business_name="My Corp",
        base_currency="USD",
        timezone="UTC",
    )
    assert biz["business_name"] == "My Corp"
    assert biz["base_currency"] == "USD"


@pytest.mark.asyncio
async def test_create_business_invalid_currency_raises(db_session: AsyncSession):
    user, _, _ = await _register(db_session, "curr@biz.com")
    bsvc = BusinessService(db_session)
    with pytest.raises(BadRequestException):
        await bsvc.create_business(
            actor_user_id=uuid.UUID(user["user_id"]),
            actor_role_in_token=None,
            business_name="Corp",
            base_currency="XXX",
            timezone="UTC",
        )


@pytest.mark.asyncio
async def test_create_business_invalid_timezone_raises(db_session: AsyncSession):
    user, _, _ = await _register(db_session, "tz@biz.com")
    bsvc = BusinessService(db_session)
    with pytest.raises(BadRequestException):
        await bsvc.create_business(
            actor_user_id=uuid.UUID(user["user_id"]),
            actor_role_in_token=None,
            business_name="Corp",
            base_currency="USD",
            timezone="Invalid/Timezone",
        )


@pytest.mark.asyncio
async def test_get_business_non_member_raises_forbidden(db_session: AsyncSession):
    owner, _, _ = await _register(db_session, "owner2@biz.com")
    stranger, _, _ = await _register(db_session, "stranger@biz.com")
    bsvc = BusinessService(db_session)
    biz = await bsvc.create_business(
        actor_user_id=uuid.UUID(owner["user_id"]),
        actor_role_in_token=None,
        business_name="Private Corp",
        base_currency="EUR",
        timezone="UTC",
    )
    with pytest.raises(ForbiddenException):
        await bsvc.get_business(
            business_id=uuid.UUID(biz["business_id"]),
            actor_user_id=uuid.UUID(stranger["user_id"]),
            actor_memberships={},
        )


@pytest.mark.asyncio
async def test_add_member_success(db_session: AsyncSession):
    owner, _, _ = await _register(db_session, "owner3@biz.com", "Owner3")
    employee, _, _ = await _register(db_session, "emp@biz.com", "Emp")
    bsvc = BusinessService(db_session)
    biz = await bsvc.create_business(
        actor_user_id=uuid.UUID(owner["user_id"]),
        actor_role_in_token=None,
        business_name="Two-Person Corp",
        base_currency="GBP",
        timezone="UTC",
    )
    biz_id = uuid.UUID(biz["business_id"])
    owner_memberships = {biz["business_id"]: "OWNER"}

    member = await bsvc.add_member(
        business_id=biz_id,
        actor_user_id=uuid.UUID(owner["user_id"]),
        actor_memberships=owner_memberships,
        target_user_id=uuid.UUID(employee["user_id"]),
        role="EMPLOYEE",
    )
    assert member["role"] == "EMPLOYEE"


@pytest.mark.asyncio
async def test_add_member_duplicate_raises_conflict(db_session: AsyncSession):
    owner, _, _ = await _register(db_session, "owner4@biz.com", "Owner4")
    emp, _, _ = await _register(db_session, "emp2@biz.com", "Emp2")
    bsvc = BusinessService(db_session)
    biz = await bsvc.create_business(
        actor_user_id=uuid.UUID(owner["user_id"]),
        actor_role_in_token=None,
        business_name="Corp4",
        base_currency="USD",
        timezone="UTC",
    )
    biz_id = uuid.UUID(biz["business_id"])
    mems = {biz["business_id"]: "OWNER"}

    await bsvc.add_member(
        business_id=biz_id, actor_user_id=uuid.UUID(owner["user_id"]),
        actor_memberships=mems, target_user_id=uuid.UUID(emp["user_id"]), role="EMPLOYEE"
    )
    with pytest.raises(ConflictException):
        await bsvc.add_member(
            business_id=biz_id, actor_user_id=uuid.UUID(owner["user_id"]),
            actor_memberships=mems, target_user_id=uuid.UUID(emp["user_id"]), role="ADMIN"
        )


@pytest.mark.asyncio
async def test_last_owner_protection(db_session: AsyncSession):
    owner, _, _ = await _register(db_session, "sole@biz.com", "Sole Owner")
    bsvc = BusinessService(db_session)
    biz = await bsvc.create_business(
        actor_user_id=uuid.UUID(owner["user_id"]),
        actor_role_in_token=None,
        business_name="Solo Corp",
        base_currency="USD",
        timezone="UTC",
    )
    biz_id = uuid.UUID(biz["business_id"])
    mems = {biz["business_id"]: "OWNER"}

    with pytest.raises(LastOwnerException):
        await bsvc.update_member(
            business_id=biz_id,
            actor_user_id=uuid.UUID(owner["user_id"]),
            actor_memberships=mems,
            target_user_id=uuid.UUID(owner["user_id"]),
            new_role="ADMIN",
        )


@pytest.mark.asyncio
async def test_employee_cannot_create_business_by_default(db_session: AsyncSession):
    user, _, _ = await _register(db_session, "employee@biz.com", "Employee")
    bsvc = BusinessService(db_session)
    with pytest.raises(ForbiddenException):
        await bsvc.create_business(
            actor_user_id=uuid.UUID(user["user_id"]),
            actor_role_in_token="EMPLOYEE",  # simulating token claim
            business_name="Forbidden Corp",
            base_currency="USD",
            timezone="UTC",
        )


@pytest.mark.asyncio
async def test_soft_delete_business(db_session: AsyncSession):
    owner, _, _ = await _register(db_session, "del@biz.com", "Deleter")
    bsvc = BusinessService(db_session)
    biz = await bsvc.create_business(
        actor_user_id=uuid.UUID(owner["user_id"]),
        actor_role_in_token=None,
        business_name="Delete Corp",
        base_currency="USD",
        timezone="UTC",
    )
    biz_id = uuid.UUID(biz["business_id"])
    mems = {biz["business_id"]: "OWNER"}

    await bsvc.delete_business(
        business_id=biz_id,
        actor_user_id=uuid.UUID(owner["user_id"]),
        actor_memberships=mems,
    )
    # Fetching the deleted business — should still exist but is_active=False
    from app.repositories.business_repo import BusinessRepository
    repo = BusinessRepository(db_session)
    record = await repo.get_by_id(biz_id)
    assert record is not None
    assert record.is_active is False
