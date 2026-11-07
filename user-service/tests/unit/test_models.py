"""Unit tests for SQLAlchemy database models and constraints."""

import uuid
from datetime import datetime, timezone
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog
from app.models.business import Business
from app.models.member import BusinessMember
from app.models.token import EmailToken, RefreshToken
from app.models.user import User


@pytest.mark.asyncio
async def test_create_user(db_session: AsyncSession):
    user = User(
        user_id=uuid.uuid4(),
        full_name="Alice Owner",
        email="Alice@Example.com",
        email_normalized="alice@example.com",
        password_hash="argon2id$mockedhash",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()

    res = await db_session.execute(select(User).where(User.email_normalized == "alice@example.com"))
    fetched = res.scalar_one_or_none()
    assert fetched is not None
    assert fetched.full_name == "Alice Owner"
    assert fetched.is_active is True
    assert fetched.failed_login_count == 0


@pytest.mark.asyncio
async def test_unique_email_normalized_constraint(db_session: AsyncSession):
    user1 = User(
        full_name="User One",
        email="test@example.com",
        email_normalized="test@example.com",
        password_hash="hash1",
    )
    user2 = User(
        full_name="User Two",
        email="TEST@example.com",
        email_normalized="test@example.com",
        password_hash="hash2",
    )
    db_session.add(user1)
    await db_session.flush()

    db_session.add(user2)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_business_and_membership_relationship(db_session: AsyncSession):
    user = User(
        full_name="Bob Builder",
        email="bob@example.com",
        email_normalized="bob@example.com",
        password_hash="hash_bob",
    )
    db_session.add(user)
    await db_session.flush()

    business = Business(
        business_name="Acme Corp",
        base_currency="USD",
        timezone="America/New_York",
        is_active=True,
    )
    db_session.add(business)
    await db_session.flush()

    membership = BusinessMember(
        business_id=business.business_id,
        user_id=user.user_id,
        role="OWNER",
        is_active=True,
    )
    db_session.add(membership)
    await db_session.flush()

    # Query with join
    res = await db_session.execute(
        select(BusinessMember).where(BusinessMember.business_id == business.business_id)
    )
    member = res.scalar_one()
    assert member.role == "OWNER"
    assert member.user_id == user.user_id


@pytest.mark.asyncio
async def test_unique_membership_per_business_and_user(db_session: AsyncSession):
    user = User(
        full_name="Carol",
        email="carol@example.com",
        email_normalized="carol@example.com",
        password_hash="hash_carol",
    )
    biz = Business(
        business_name="Carol Enterprises",
        base_currency="EUR",
        timezone="Europe/Paris",
    )
    db_session.add_all([user, biz])
    await db_session.flush()

    mem1 = BusinessMember(business_id=biz.business_id, user_id=user.user_id, role="OWNER")
    mem2 = BusinessMember(business_id=biz.business_id, user_id=user.user_id, role="ADMIN")

    db_session.add(mem1)
    await db_session.flush()

    db_session.add(mem2)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_refresh_token_and_email_token(db_session: AsyncSession):
    user = User(
        full_name="Dave",
        email="dave@example.com",
        email_normalized="dave@example.com",
        password_hash="hash_dave",
    )
    db_session.add(user)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    family_id = uuid.uuid4()
    rf = RefreshToken(
        user_id=user.user_id,
        family_id=family_id,
        token_hash="a" * 64,
        expires_at=now,
    )
    db_session.add(rf)

    em = EmailToken(
        user_id=user.user_id,
        token_hash="b" * 64,
        purpose="EMAIL_VERIFICATION",
        expires_at=now,
    )
    db_session.add(em)
    await db_session.flush()

    res_rf = await db_session.execute(select(RefreshToken).where(RefreshToken.user_id == user.user_id))
    assert res_rf.scalar_one().token_hash == "a" * 64

    res_em = await db_session.execute(select(EmailToken).where(EmailToken.user_id == user.user_id))
    assert res_em.scalar_one().purpose == "EMAIL_VERIFICATION"


@pytest.mark.asyncio
async def test_audit_log_creation(db_session: AsyncSession):
    audit = AuditLog(
        actor_type="USER",
        actor_id=str(uuid.uuid4()),
        action="USER_REGISTERED",
        entity_type="user",
        entity_id=uuid.uuid4(),
        before=None,
        after={"email": "audit@example.com"},
        request_id="req-audit-123",
    )
    db_session.add(audit)
    await db_session.flush()

    res = await db_session.execute(select(AuditLog).where(AuditLog.request_id == "req-audit-123"))
    entry = res.scalar_one()
    assert entry.action == "USER_REGISTERED"
    assert entry.after == {"email": "audit@example.com"}
