"""Initial invoice service schema with restricted roles and audit triggers

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-08

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. invoices
    op.create_table(
        "invoices",
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_number", sa.String(50), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("subtotal", sa.Numeric(19, 4), nullable=False, server_default="0.0000"),
        sa.Column("tax_amount", sa.Numeric(19, 4), nullable=False, server_default="0.0000"),
        sa.Column("total_amount", sa.Numeric(19, 4), nullable=False, server_default="0.0000"),
        sa.Column("amount_paid", sa.Numeric(19, 4), nullable=False, server_default="0.0000"),
        sa.Column("business_timezone", sa.String(50), nullable=True),
        sa.Column("customer_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("seller_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("pay_token_hash", sa.CHAR(64), nullable=True, unique=True),
        sa.Column("min_installment_amount", sa.Numeric(19, 4), nullable=True),
        sa.Column("notes", sa.String(1000), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("void_reason", sa.String(255), nullable=True),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'ISSUED', 'PARTIALLY_PAID', 'PAID', 'VOID')",
            name="chk_invoices_status_valid",
        ),
        sa.CheckConstraint("amount_paid >= 0", name="chk_invoices_amount_paid_non_negative"),
        sa.CheckConstraint("amount_paid <= total_amount", name="chk_invoices_amount_paid_le_total"),
        sa.CheckConstraint("total_amount >= 0", name="chk_invoices_total_amount_non_negative"),
        sa.CheckConstraint(
            "issue_date IS NULL OR due_date >= issue_date",
            name="chk_invoices_due_date_ge_issue_date",
        ),
        sa.CheckConstraint(
            "(status = 'DRAFT' OR (status = 'VOID' AND issued_at IS NULL)) OR ("
            "invoice_number IS NOT NULL AND "
            "issue_date IS NOT NULL AND "
            "customer_snapshot IS NOT NULL AND "
            "seller_snapshot IS NOT NULL AND "
            "business_timezone IS NOT NULL)",
            name="chk_invoices_issued_fields_not_null",
        ),
        sa.CheckConstraint(
            "status <> 'VOID' OR ("
            "void_reason IS NOT NULL AND "
            "voided_at IS NOT NULL AND "
            "voided_by IS NOT NULL)",
            name="chk_invoices_void_fields_not_null",
        ),
        sa.CheckConstraint(
            "min_installment_amount IS NULL OR ("
            "min_installment_amount > 0 AND "
            "min_installment_amount <= total_amount)",
            name="chk_invoices_min_installment_valid",
        ),
        sa.UniqueConstraint("business_id", "invoice_number", name="uq_invoices_business_number"),
    )
    op.create_index("ix_invoices_business_status", "invoices", ["business_id", "status"])
    op.create_index("ix_invoices_business_customer", "invoices", ["business_id", "customer_id"])
    op.create_index("ix_invoices_business_due_date", "invoices", ["business_id", "due_date"])
    op.create_index("ix_invoices_business_created_at", "invoices", ["business_id", "created_at"])

    # 2. invoice_items
    op.create_table(
        "invoice_items",
        sa.Column("item_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "invoice_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("invoices.invoice_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(255), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 4), nullable=False),
        sa.Column("unit_price", sa.Numeric(19, 4), nullable=False),
        sa.Column("tax_rate", sa.Numeric(7, 4), nullable=False),
        sa.Column("line_subtotal", sa.Numeric(19, 4), nullable=False),
        sa.Column("line_tax", sa.Numeric(19, 4), nullable=False),
        sa.Column("line_total", sa.Numeric(19, 4), nullable=False),
        sa.CheckConstraint("quantity > 0", name="chk_invoice_items_quantity_positive"),
        sa.CheckConstraint("unit_price >= 0", name="chk_invoice_items_unit_price_non_negative"),
        sa.CheckConstraint("tax_rate >= 0", name="chk_invoice_items_tax_rate_non_negative"),
        sa.UniqueConstraint("invoice_id", "position", name="uq_invoice_items_invoice_position"),
    )

    # 3. payment_allocations
    op.create_table(
        "payment_allocations",
        sa.Column("allocation_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "invoice_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("invoices.invoice_id"),
            nullable=False,
        ),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("allocated_amount", sa.Numeric(19, 4), nullable=False),
        sa.Column("allocated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("allocated_amount > 0", name="chk_payment_allocations_amount_positive"),
    )

    # 4. processed_events (inbox)
    op.create_table(
        "processed_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("outcome", sa.String(30), nullable=False),
        sa.CheckConstraint("outcome IN ('APPLIED', 'REJECTED')", name="chk_processed_events_outcome"),
    )

    # 5. reconciliation_exceptions
    op.create_table(
        "reconciliation_exceptions",
        sa.Column("exception_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason_code", sa.String(50), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="OPEN"),
        sa.Column("resolved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "reason_code IN ('INVOICE_VOID','INVOICE_NOT_PAYABLE','CURRENCY_MISMATCH','AMOUNT_EXCEEDS_BALANCE','BUSINESS_MISMATCH','INVOICE_NOT_FOUND','INVALID_AMOUNT')",
            name="chk_reconciliation_exceptions_reason",
        ),
        sa.CheckConstraint("status IN ('OPEN', 'RESOLVED')", name="chk_reconciliation_exceptions_status"),
    )

    # 6. invoice_settings
    op.create_table(
        "invoice_settings",
        sa.Column("business_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("invoice_prefix", sa.String(10), nullable=False, server_default="INV-"),
        sa.Column("seller_name", sa.String(150), nullable=True),
        sa.Column("seller_address", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("seller_tax_id", sa.String(50), nullable=True),
        sa.Column("default_due_days", sa.Integer(), nullable=False, server_default="30"),
    )

    # 7. tax_rates
    op.create_table(
        "tax_rates",
        sa.Column("tax_rate_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("rate", sa.Numeric(7, 4), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.CheckConstraint("rate >= 0", name="chk_tax_rates_rate_non_negative"),
        sa.UniqueConstraint("business_id", "rate", "name", name="uq_tax_rates_business_rate_name"),
    )

    # 8. invoice_counters
    op.create_table(
        "invoice_counters",
        sa.Column("business_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("last_number", sa.BigInteger(), nullable=False, server_default="0"),
    )

    # 9. idempotency_keys
    op.create_table(
        "idempotency_keys",
        sa.Column("business_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("endpoint", sa.String(100), primary_key=True),
        sa.Column("request_hash", sa.CHAR(64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # 10. audit_log
    op.create_table(
        "audit_log",
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_type", sa.String(20), nullable=False),
        sa.Column("actor_id", sa.String(100), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("request_id", sa.String(100), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # 11. Immutability triggers on audit_log
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_audit_log_immutable()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only: updates and deletes are forbidden';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_log_immutable
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION fn_audit_log_immutable();
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fn_audit_log_no_truncate()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only: truncate is forbidden';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_log_no_truncate
        BEFORE TRUNCATE ON audit_log
        FOR EACH STATEMENT EXECUTE FUNCTION fn_audit_log_no_truncate();
        """
    )

    # 12. Apply database role permissions for invoice_app
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'invoice_app') THEN
                GRANT USAGE ON SCHEMA public TO invoice_app;
                
                -- Full read/write on normal mutable tables
                GRANT SELECT, INSERT, UPDATE ON invoices TO invoice_app;
                GRANT SELECT, INSERT, UPDATE, DELETE ON invoice_items TO invoice_app;
                GRANT SELECT, INSERT, UPDATE ON invoice_settings TO invoice_app;
                GRANT SELECT, INSERT, UPDATE ON tax_rates TO invoice_app;
                GRANT SELECT, INSERT, UPDATE ON invoice_counters TO invoice_app;
                GRANT SELECT, INSERT, UPDATE ON idempotency_keys TO invoice_app;
                GRANT SELECT, INSERT, UPDATE ON reconciliation_exceptions TO invoice_app;

                -- Append-only tables: SELECT and INSERT only. No UPDATE, no DELETE, no TRUNCATE
                GRANT SELECT, INSERT ON audit_log TO invoice_app;
                GRANT SELECT, INSERT ON processed_events TO invoice_app;
                GRANT SELECT, INSERT ON payment_allocations TO invoice_app;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # Revoke grants
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'invoice_app') THEN
                REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM invoice_app;
            END IF;
        END $$;
        """
    )

    # Drop triggers and functions
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_no_truncate ON audit_log;")
    op.execute("DROP FUNCTION IF EXISTS fn_audit_log_no_truncate();")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_immutable ON audit_log;")
    op.execute("DROP FUNCTION IF EXISTS fn_audit_log_immutable();")

    # Drop tables in reverse dependency order
    op.drop_table("audit_log")
    op.drop_table("idempotency_keys")
    op.drop_table("invoice_counters")
    op.drop_table("tax_rates")
    op.drop_table("invoice_settings")
    op.drop_table("reconciliation_exceptions")
    op.drop_table("processed_events")
    op.drop_table("payment_allocations")
    op.drop_table("invoice_items")
    op.drop_index("ix_invoices_business_created_at", table_name="invoices")
    op.drop_index("ix_invoices_business_due_date", table_name="invoices")
    op.drop_index("ix_invoices_business_customer", table_name="invoices")
    op.drop_index("ix_invoices_business_status", table_name="invoices")
    op.drop_table("invoices")
