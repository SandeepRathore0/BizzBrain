import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from sqlalchemy import (
    CHAR,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, utc_now


class Invoice(Base):
    __tablename__ = "invoices"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    invoice_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="DRAFT",
    )
    issue_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)

    subtotal: Mapped[Decimal] = mapped_column(
        Numeric(19, 4), nullable=False, default=Decimal("0.0000")
    )
    tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(19, 4), nullable=False, default=Decimal("0.0000")
    )
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(19, 4), nullable=False, default=Decimal("0.0000")
    )
    amount_paid: Mapped[Decimal] = mapped_column(
        Numeric(19, 4), nullable=False, default=Decimal("0.0000")
    )

    business_timezone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    customer_snapshot: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    seller_snapshot: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    pay_token_hash: Mapped[Optional[str]] = mapped_column(CHAR(64), unique=True, nullable=True)
    min_installment_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(19, 4), nullable=True
    )
    notes: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)

    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    issued_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    voided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    voided_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    void_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Relationships
    items: Mapped[List["InvoiceItem"]] = relationship(
        "InvoiceItem",
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceItem.position",
        lazy="selectin",
    )
    allocations: Mapped[List["PaymentAllocation"]] = relationship(
        "PaymentAllocation",
        back_populates="invoice",
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT', 'ISSUED', 'PARTIALLY_PAID', 'PAID', 'VOID')",
            name="chk_invoices_status_valid",
        ),
        CheckConstraint("amount_paid >= 0", name="chk_invoices_amount_paid_non_negative"),
        CheckConstraint("amount_paid <= total_amount", name="chk_invoices_amount_paid_le_total"),
        CheckConstraint("total_amount >= 0", name="chk_invoices_total_amount_non_negative"),
        CheckConstraint(
            "issue_date IS NULL OR due_date >= issue_date",
            name="chk_invoices_due_date_ge_issue_date",
        ),
        CheckConstraint(
            "(status = 'DRAFT' OR (status = 'VOID' AND issued_at IS NULL)) OR ("
            "invoice_number IS NOT NULL AND "
            "issue_date IS NOT NULL AND "
            "customer_snapshot IS NOT NULL AND "
            "seller_snapshot IS NOT NULL AND "
            "business_timezone IS NOT NULL)",
            name="chk_invoices_issued_fields_not_null",
        ),
        CheckConstraint(
            "status <> 'VOID' OR ("
            "void_reason IS NOT NULL AND "
            "voided_at IS NOT NULL AND "
            "voided_by IS NOT NULL)",
            name="chk_invoices_void_fields_not_null",
        ),
        CheckConstraint(
            "min_installment_amount IS NULL OR ("
            "min_installment_amount > 0 AND "
            "min_installment_amount <= total_amount)",
            name="chk_invoices_min_installment_valid",
        ),
        UniqueConstraint("business_id", "invoice_number", name="uq_invoices_business_number"),
        Index("ix_invoices_business_status", "business_id", "status"),
        Index("ix_invoices_business_customer", "business_id", "customer_id"),
        Index("ix_invoices_business_due_date", "business_id", "due_date"),
        Index("ix_invoices_business_created_at", "business_id", "created_at"),
    )


class InvoiceItem(Base):
    __tablename__ = "invoice_items"

    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.invoice_id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    line_subtotal: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    line_tax: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)

    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="items")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="chk_invoice_items_quantity_positive"),
        CheckConstraint("unit_price >= 0", name="chk_invoice_items_unit_price_non_negative"),
        CheckConstraint("tax_rate >= 0", name="chk_invoice_items_tax_rate_non_negative"),
        UniqueConstraint("invoice_id", "position", name="uq_invoice_items_invoice_position"),
    )


class PaymentAllocation(Base):
    __tablename__ = "payment_allocations"

    allocation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.invoice_id"),
        nullable=False,
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True
    )
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    allocated_amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    allocated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="allocations")

    __table_args__ = (
        CheckConstraint(
            "allocated_amount > 0", name="chk_payment_allocations_amount_positive"
        ),
    )


class ProcessedEvent(Base):
    __tablename__ = "processed_events"

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)

    __table_args__ = (
        CheckConstraint("outcome IN ('APPLIED', 'REJECTED')", name="chk_processed_events_outcome"),
    )


class ReconciliationException(Base):
    __tablename__ = "reconciliation_exceptions"

    exception_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    payment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    invoice_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    reason_code: Mapped[str] = mapped_column(String(50), nullable=False)
    details: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN")
    resolved_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        CheckConstraint(
            "reason_code IN ('INVOICE_VOID','INVOICE_NOT_PAYABLE','CURRENCY_MISMATCH','AMOUNT_EXCEEDS_BALANCE','BUSINESS_MISMATCH','INVOICE_NOT_FOUND','INVALID_AMOUNT')",
            name="chk_reconciliation_exceptions_reason",
        ),
        CheckConstraint(
            "status IN ('OPEN', 'RESOLVED')",
            name="chk_reconciliation_exceptions_status",
        ),
    )


class InvoiceSettings(Base):
    __tablename__ = "invoice_settings"

    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    invoice_prefix: Mapped[str] = mapped_column(String(10), nullable=False, default="INV-")
    seller_name: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    seller_address: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    seller_tax_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    default_due_days: Mapped[int] = mapped_column(nullable=False, default=30)


class TaxRate(Base):
    __tablename__ = "tax_rates"

    tax_rate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)

    __table_args__ = (
        CheckConstraint("rate >= 0", name="chk_tax_rates_rate_non_negative"),
        UniqueConstraint("business_id", "rate", "name", name="uq_tax_rates_business_rate_name"),
    )


class InvoiceCounter(Base):
    __tablename__ = "invoice_counters"

    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    last_number: Mapped[int] = mapped_column(nullable=False, default=0)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(100), primary_key=True)
    request_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    response_status: Mapped[int] = mapped_column(nullable=False)
    response_body: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    business_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    before: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    after: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
