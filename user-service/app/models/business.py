"""Business tenant SQLAlchemy model."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List
from sqlalchemy import Boolean, CheckConstraint, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import CHAR

from app.models.base import Base, GUID

if TYPE_CHECKING:
    from app.models.member import BusinessMember


class Business(Base):
    """Business tenant entity."""

    __tablename__ = "businesses"

    business_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        primary_key=True,
        default=uuid.uuid4,
    )
    business_name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )
    base_currency: Mapped[str] = mapped_column(
        CHAR(3),
        nullable=False,
    )
    timezone: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint("base_currency ~ '^[A-Z]{3}$'", name="check_business_base_currency_format"),
    )

    # Relationships
    memberships: Mapped[List["BusinessMember"]] = relationship(
        "BusinessMember",
        back_populates="business",
        cascade="all, delete-orphan",
    )
