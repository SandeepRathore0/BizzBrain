import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class PaymentProviderReference(Base):
    __tablename__ = "payment_provider_references"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payments.payment_id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    provider_order_id: Mapped[str] = mapped_column(
        String, unique=True, nullable=False, index=True
    )
    provider_capture_id: Mapped[Optional[str]] = mapped_column(
        String, unique=True, nullable=True, index=True
    )
    provider_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    paypal_request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    capture_request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    raw_last_response: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("now()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    payment = relationship("Payment", back_populates="provider_reference")
