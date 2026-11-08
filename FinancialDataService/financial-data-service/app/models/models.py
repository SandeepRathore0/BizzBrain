import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class Customer(Base):
    __tablename__ = "customers"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    billing_address: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    tax_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_customers_business_id_is_active", "business_id", "is_active"),
        Index("ix_customers_business_id_lower_name", "business_id", text("lower(name)")),
    )


class Supplier(Base):
    __tablename__ = "suppliers"

    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    address: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    expenses: Mapped[list["Expense"]] = relationship("Expense", back_populates="supplier")

    __table_args__ = (
        Index("ix_suppliers_business_id_is_active", "business_id", "is_active"),
        Index("ix_suppliers_business_id_lower_name", "business_id", text("lower(name)")),
    )


class ExpenseCategory(Base):
    __tablename__ = "expense_categories"

    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    category_name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    expenses: Mapped[list["Expense"]] = relationship("Expense", back_populates="category")

    __table_args__ = (
        Index(
            "uq_expense_categories_business_id_lower_name",
            "business_id",
            text("lower(category_name)"),
            unique=True,
        ),
    )


class Expense(Base):
    __tablename__ = "expenses"

    expense_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    supplier_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.supplier_id"), nullable=True
    )
    category_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("expense_categories.category_id"), nullable=True
    )
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    receipt_file_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="POSTED", nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    voided_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    voided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    void_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    supplier: Mapped[Optional[Supplier]] = relationship("Supplier", back_populates="expenses")
    category: Mapped[Optional[ExpenseCategory]] = relationship("ExpenseCategory", back_populates="expenses")
    receipts: Mapped[list["ExpenseReceipt"]] = relationship("ExpenseReceipt", back_populates="expense")

    __table_args__ = (
        UniqueConstraint("business_id", "idempotency_key", name="uq_expenses_business_idempotency"),
        CheckConstraint("amount > 0", name="ck_expenses_amount_positive"),
        CheckConstraint("status IN ('POSTED', 'VOID')", name="ck_expenses_status_valid"),
        CheckConstraint(
            "(status != 'VOID') OR (voided_by IS NOT NULL AND voided_at IS NOT NULL AND void_reason IS NOT NULL)",
            name="ck_expenses_void_fields_set",
        ),
        Index("ix_expenses_business_id_expense_date", "business_id", "expense_date"),
        Index("ix_expenses_business_id_category_id", "business_id", "category_id"),
        Index("ix_expenses_business_id_supplier_id", "business_id", "supplier_id"),
        Index("ix_expenses_business_id_status", "business_id", "status"),
    )


class ExpenseReceipt(Base):
    __tablename__ = "expense_receipts"

    receipt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    expense_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("expenses.expense_id"), nullable=False
    )
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    file_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    expense: Mapped[Expense] = relationship("Expense", back_populates="receipts")

    __table_args__ = (
        Index("ix_expense_receipts_expense_id", "expense_id"),
        Index("ix_expense_receipts_business_id", "business_id"),
    )


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(255), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_idempotency_keys_created_at", "created_at"),
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    business_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)  # USER, SERVICE, SYSTEM
    actor_id: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    before: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    after: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    request_id: Mapped[str] = mapped_column(String(100), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_audit_log_business_id_occurred_at", "business_id", "occurred_at"),
        Index("ix_audit_log_entity", "entity_type", "entity_id"),
    )
