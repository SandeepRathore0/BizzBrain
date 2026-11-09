"""Initial schema for payment service

Revision ID: 0001_initial_schema
Revises: 
Create Date: 2026-11-08 12:00:00.000000

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
    # 1. Create payments table
    op.create_table(
        "payments",
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_number", sa.String(length=50), nullable=True),
        sa.Column("amount", sa.Numeric(precision=19, scale=4), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False, server_default="PAYPAL"),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("allocation_status", sa.String(length=20), nullable=False, server_default="NOT_APPLICABLE"),
        sa.Column("status_reason", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("initiated_by_type", sa.String(length=20), nullable=False),
        sa.Column("initiated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("amount > 0", name="chk_payments_amount_positive"),
        sa.CheckConstraint(
            "status IN ('CREATED','PAYER_APPROVED','CAPTURE_PENDING','COMPLETED','FAILED','CANCELLED','EXPIRED','REQUIRES_REVIEW','REFUNDED')",
            name="chk_payments_status_valid",
        ),
        sa.CheckConstraint(
            "allocation_status IN ('NOT_APPLICABLE','PENDING','ALLOCATED','REJECTED')",
            name="chk_payments_allocation_status_valid",
        ),
        sa.CheckConstraint(
            "initiated_by_type IN ('PUBLIC_LINK','USER')",
            name="chk_payments_initiated_by_type_valid",
        ),
        sa.CheckConstraint(
            "(allocation_status = 'NOT_APPLICABLE' AND status NOT IN ('COMPLETED','REFUNDED')) OR "
            "(allocation_status <> 'NOT_APPLICABLE' AND status IN ('COMPLETED','REFUNDED'))",
            name="chk_payments_allocation_status_coherence",
        ),
        sa.UniqueConstraint("business_id", "idempotency_key", name="uq_payments_business_idempotency"),
    )
    op.create_index("ix_payments_business_created", "payments", ["business_id", "created_at"])
    op.create_index("ix_payments_business_invoice", "payments", ["business_id", "invoice_id"])
    op.create_index("ix_payments_status_expires", "payments", ["status", "expires_at"])
    op.create_index(
        "uq_payments_active_invoice",
        "payments",
        ["invoice_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('CREATED','PAYER_APPROVED','CAPTURE_PENDING','REQUIRES_REVIEW')"),
    )

    # 2. Create payment_provider_references table
    op.create_table(
        "payment_provider_references",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "payment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payments.payment_id", ondelete="CASCADE"),
            unique=True,
            nullable=False,
        ),
        sa.Column("provider_order_id", sa.String(), unique=True, nullable=False),
        sa.Column("provider_capture_id", sa.String(), unique=True, nullable=True),
        sa.Column("provider_status", sa.String(), nullable=True),
        sa.Column("paypal_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("capture_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("raw_last_response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_provider_references_order_id", "payment_provider_references", ["provider_order_id"])
    op.create_index("ix_provider_references_capture_id", "payment_provider_references", ["provider_capture_id"])

    # 3. Create payment_webhook_events table
    op.create_table(
        "payment_webhook_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider_event_id", sa.String(), unique=True, nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column(
            "payment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payments.payment_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("processing_status", sa.String(length=20), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "processing_status IN ('RECEIVED','PROCESSED','IGNORED','FAILED')",
            name="chk_webhook_events_processing_status",
        ),
    )
    op.create_index("ix_webhook_events_provider_event_id", "payment_webhook_events", ["provider_event_id"])
    op.create_index("ix_webhook_events_event_type", "payment_webhook_events", ["event_type"])

    # 4. Create outbox_events table
    op.create_table(
        "outbox_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING','IN_FLIGHT','DELIVERED','DEAD')",
            name="chk_outbox_events_status",
        ),
        sa.UniqueConstraint("event_type", "aggregate_id", name="uq_outbox_event_type_aggregate"),
    )
    op.create_index("ix_outbox_aggregate_id", "outbox_events", ["aggregate_id"])
    op.create_index("ix_outbox_status_next_retry", "outbox_events", ["status", "next_retry_at"])

    # 5. Create idempotency_keys table
    op.create_table(
        "idempotency_keys",
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("endpoint", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("business_id", "key", "endpoint", name="pk_idempotency_keys"),
    )

    # 6. Create audit_log table
    op.create_table(
        "audit_log",
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_type", sa.String(length=50), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=100), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("request_id", sa.String(length=100), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_audit_log_business_id", "audit_log", ["business_id"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_entity_id", "audit_log", ["entity_id"])
    op.create_index("ix_audit_log_business_occurred", "audit_log", ["business_id", "occurred_at"])

    # 7. Immutable trigger on audit_log
    op.execute("""
        CREATE OR REPLACE FUNCTION protect_audit_log_immutable()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only: UPDATE, DELETE, and TRUNCATE operations are prohibited.';
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER trg_protect_audit_log_immutable
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW
        EXECUTE FUNCTION protect_audit_log_immutable();
    """)

    op.execute("""
        CREATE TRIGGER trg_protect_audit_log_truncate
        BEFORE TRUNCATE ON audit_log
        FOR EACH STATEMENT
        EXECUTE FUNCTION protect_audit_log_immutable();
    """)

    # 8. Grant privileges to payment_app role
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'payment_app') THEN
                GRANT SELECT, INSERT, UPDATE ON payments, payment_provider_references, payment_webhook_events, outbox_events, idempotency_keys TO payment_app;
                GRANT SELECT, INSERT ON audit_log TO payment_app;
                REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM payment_app;
                REVOKE DELETE, TRUNCATE ON payments, payment_provider_references, payment_webhook_events, outbox_events, idempotency_keys FROM payment_app;
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_protect_audit_log_truncate ON audit_log;")
    op.execute("DROP TRIGGER IF EXISTS trg_protect_audit_log_immutable ON audit_log;")
    op.execute("DROP FUNCTION IF EXISTS protect_audit_log_immutable();")

    op.drop_table("audit_log")
    op.drop_table("idempotency_keys")
    op.drop_table("outbox_events")
    op.drop_table("payment_webhook_events")
    op.drop_table("payment_provider_references")
    op.drop_table("payments")
