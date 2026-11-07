"""Append-only Audit Log SQLAlchemy model."""

import uuid
from datetime import datetime
from typing import Any, Dict, Optional
from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.base import Base, GUID


class AuditLog(Base):
    """
    Append-only audit trail.
    Application DB role must have no UPDATE or DELETE grant on this table.
    """

    __tablename__ = "audit_log"

    audit_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        primary_key=True,
        default=uuid.uuid4,
    )
    business_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        nullable=True,
        index=True,
    )
    actor_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )  # e.g., 'USER', 'SERVICE', 'SYSTEM', 'ANONYMOUS'
    actor_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )  # e.g., 'USER_REGISTERED', 'LOGIN_SUCCESS', 'ROLE_CHANGED'
    entity_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )  # e.g., 'user', 'business', 'business_member'
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        nullable=True,
    )
    before: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONB().with_variant(JSON, "sqlite"),
        nullable=True,
    )
    after: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONB().with_variant(JSON, "sqlite"),
        nullable=True,
    )
    request_id: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
