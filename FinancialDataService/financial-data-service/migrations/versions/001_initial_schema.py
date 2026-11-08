"""Initial database schema for financial_db

Revision ID: 001_initial_schema
Revises: 
Create Date: 2026-11-08 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. customers table
    op.create_table(
        "customers",
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=30), nullable=True),
        sa.Column("billing_address", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tax_id", sa.String(length=50), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "ix_customers_business_id_is_active",
        "customers",
        ["business_id", "is_active"],
        unique=False,
    )
    op.create_index(
        "ix_customers_business_id_lower_name",
        "customers",
        ["business_id", sa.text("lower(name)")],
        unique=False,
    )

    # 2. suppliers table
    op.create_table(
        "suppliers",
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=30), nullable=True),
        sa.Column("address", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "ix_suppliers_business_id_is_active",
        "suppliers",
        ["business_id", "is_active"],
        unique=False,
    )
    op.create_index(
        "ix_suppliers_business_id_lower_name",
        "suppliers",
        ["business_id", sa.text("lower(name)")],
        unique=False,
    )

    # 3. expense_categories table
    op.create_table(
        "expense_categories",
        sa.Column("category_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category_name", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.create_index(
        "uq_expense_categories_business_id_lower_name",
        "expense_categories",
        ["business_id", sa.text("lower(category_name)")],
        unique=True,
    )

    # 4. expenses table
    op.create_table(
        "expenses",
        sa.Column("expense_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("amount", sa.Numeric(precision=19, scale=4), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("expense_date", sa.Date(), nullable=False),
        sa.Column("receipt_file_key", sa.String(length=512), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="POSTED", nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("voided_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("void_reason", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["expense_categories.category_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.supplier_id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("business_id", "idempotency_key", name="uq_expenses_business_idempotency"),
        sa.CheckConstraint("amount > 0", name="ck_expenses_amount_positive"),
        sa.CheckConstraint("status IN ('POSTED', 'VOID')", name="ck_expenses_status_valid"),
        sa.CheckConstraint(
            "(status != 'VOID') OR (voided_by IS NOT NULL AND voided_at IS NOT NULL AND void_reason IS NOT NULL)",
            name="ck_expenses_void_fields_set",
        ),
    )
    op.create_index("ix_expenses_business_id_expense_date", "expenses", ["business_id", "expense_date"], unique=False)
    op.create_index("ix_expenses_business_id_category_id", "expenses", ["business_id", "category_id"], unique=False)
    op.create_index("ix_expenses_business_id_supplier_id", "expenses", ["business_id", "supplier_id"], unique=False)
    op.create_index("ix_expenses_business_id_status", "expenses", ["business_id", "status"], unique=False)

    # 5. expense_receipts table
    op.create_table(
        "expense_receipts",
        sa.Column("receipt_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("expense_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_key", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["expense_id"], ["expenses.expense_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_expense_receipts_expense_id", "expense_receipts", ["expense_id"], unique=False)
    op.create_index("ix_expense_receipts_business_id", "expense_receipts", ["business_id"], unique=False)

    # 6. idempotency_keys table
    op.create_table(
        "idempotency_keys",
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("endpoint", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("business_id", "key", "endpoint"),
    )
    op.create_index("ix_idempotency_keys_created_at", "idempotency_keys", ["created_at"], unique=False)

    # 7. audit_log table
    op.create_table(
        "audit_log",
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("actor_id", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("request_id", sa.String(length=100), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_audit_log_business_id_occurred_at", "audit_log", ["business_id", "occurred_at"], unique=False)
    op.create_index("ix_audit_log_entity", "audit_log", ["entity_type", "entity_id"], unique=False)


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("idempotency_keys")
    op.drop_table("expense_receipts")
    op.drop_table("expenses")
    op.drop_table("expense_categories")
    op.drop_table("suppliers")
    op.drop_table("customers")
