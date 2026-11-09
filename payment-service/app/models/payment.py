import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from sqlalchemy import (
    CHAR,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Payment(Base):
    __tablename__ = "payments"

    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    invoice_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False, default="PAYPAL")
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    allocation_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="NOT_APPLICABLE"
    )
    status_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    initiated_by_type: Mapped[str] = mapped_column(String(20), nullable=False)
    initiated_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("now()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    provider_reference = relationship(
        "PaymentProviderReference",
        back_populates="payment",
        uselist=False,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint("amount > 0", name="chk_payments_amount_positive"),
        CheckConstraint(
            "status IN ('CREATED','PAYER_APPROVED','CAPTURE_PENDING','COMPLETED','FAILED','CANCELLED','EXPIRED','REQUIRES_REVIEW','REFUNDED')",
            name="chk_payments_status_valid",
        ),
        CheckConstraint(
            "allocation_status IN ('NOT_APPLICABLE','PENDING','ALLOCATED','REJECTED')",
            name="chk_payments_allocation_status_valid",
        ),
        CheckConstraint(
            "initiated_by_type IN ('PUBLIC_LINK','USER')",
            name="chk_payments_initiated_by_type_valid",
        ),
        CheckConstraint(
            "(allocation_status = 'NOT_APPLICABLE' AND status NOT IN ('COMPLETED','REFUNDED')) OR "
            "(allocation_status <> 'NOT_APPLICABLE' AND status IN ('COMPLETED','REFUNDED'))",
            name="chk_payments_allocation_status_coherence",
        ),
        UniqueConstraint("business_id", "idempotency_key", name="uq_payments_business_idempotency"),
        Index("ix_payments_business_created", "business_id", "created_at"),
        Index("ix_payments_business_invoice", "business_id", "invoice_id"),
        Index("ix_payments_status_expires", "status", "expires_at"),
        Index(
            "uq_payments_active_invoice",
            "invoice_id",
            unique=True,
            postgresql_where=text(
                "status IN ('CREATED','PAYER_APPROVED','CAPTURE_PENDING','REQUIRES_REVIEW')"
            ),
        ),
    )
