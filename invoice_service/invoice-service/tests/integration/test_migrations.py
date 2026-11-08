import os
from decimal import Decimal
from uuid import uuid4
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

ALEMBIC_INI_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "alembic.ini")
)


def get_alembic_config() -> Config:
    cfg = Config(ALEMBIC_INI_PATH)
    cfg.set_main_option(
        "script_location",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "migrations")),
    )
    return cfg


def test_migration_upgrade_head(owner_engine):
    """Run alembic upgrade head using invoice_owner."""
    cfg = get_alembic_config()
    command.upgrade(cfg, "head")

    with owner_engine.connect() as conn:
        tables = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name;"
            )
        ).fetchall()
        table_names = [t[0] for t in tables]
        expected_tables = [
            "alembic_version",
            "audit_log",
            "idempotency_keys",
            "invoice_counters",
            "invoice_items",
            "invoice_settings",
            "invoices",
            "payment_allocations",
            "processed_events",
            "reconciliation_exceptions",
            "tax_rates",
        ]
        for expected in expected_tables:
            assert expected in table_names, f"Expected table {expected} not found in public schema"


def test_restricted_runtime_role_permissions(app_sync_engine):
    """Verify that invoice_app role has the exact allowed and disallowed permissions."""
    biz_id = str(uuid4())
    inv_id = str(uuid4())
    item_id = str(uuid4())
    audit_id = str(uuid4())
    event_id = str(uuid4())
    pay_id = str(uuid4())
    alloc_id = str(uuid4())

    with app_sync_engine.begin() as conn:
        # 1. invoice_app CAN insert into invoices
        conn.execute(
            text(
                """
                INSERT INTO invoices (invoice_id, business_id, customer_id, due_date, currency, status)
                VALUES (:inv_id, :biz_id, :cust_id, '2026-12-01', 'USD', 'DRAFT')
                """
            ),
            {"inv_id": inv_id, "biz_id": biz_id, "cust_id": str(uuid4())},
        )

        # 2. invoice_app CAN insert and delete draft invoice_items
        conn.execute(
            text(
                """
                INSERT INTO invoice_items (item_id, invoice_id, position, description, quantity, unit_price, tax_rate, line_subtotal, line_tax, line_total)
                VALUES (:item_id, :inv_id, 1, 'Consulting', 1.0, 100.0, 0.18, 100.0, 18.0, 118.0)
                """
            ),
            {"item_id": item_id, "inv_id": inv_id},
        )
        # Delete draft item is allowed
        conn.execute(
            text("DELETE FROM invoice_items WHERE item_id = :item_id"),
            {"item_id": item_id},
        )

        # 3. invoice_app CAN insert into audit_log
        conn.execute(
            text(
                """
                INSERT INTO audit_log (audit_id, business_id, actor_type, actor_id, action, entity_type, entity_id)
                VALUES (:audit_id, :biz_id, 'USER', 'user-1', 'INVOICE_CREATED', 'INVOICE', :inv_id)
                """
            ),
            {"audit_id": audit_id, "biz_id": biz_id, "inv_id": inv_id},
        )

    # 4. invoice_app CANNOT UPDATE audit_log (blocked by grants and trigger)
    with app_sync_engine.connect() as conn:
        with pytest.raises(DBAPIError) as exc_info:
            conn.execute(
                text("UPDATE audit_log SET action = 'HACKED' WHERE audit_id = :audit_id"),
                {"audit_id": audit_id},
            )
        assert "permission denied" in str(exc_info.value).lower() or "append-only" in str(exc_info.value).lower()

    # 5. invoice_app CANNOT DELETE from audit_log
    with app_sync_engine.connect() as conn:
        with pytest.raises(DBAPIError) as exc_info:
            conn.execute(
                text("DELETE FROM audit_log WHERE audit_id = :audit_id"),
                {"audit_id": audit_id},
            )
        assert "permission denied" in str(exc_info.value).lower() or "append-only" in str(exc_info.value).lower()

    # 6. invoice_app CANNOT TRUNCATE audit_log
    with app_sync_engine.connect() as conn:
        with pytest.raises(DBAPIError):
            conn.execute(text("TRUNCATE TABLE audit_log"))

    # 7. invoice_app CAN insert into processed_events
    with app_sync_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO processed_events (event_id, event_type, outcome)
                VALUES (:event_id, 'PaymentCaptured', 'APPLIED')
                """
            ),
            {"event_id": event_id},
        )

    # 8. invoice_app CANNOT UPDATE or DELETE processed_events
    with app_sync_engine.connect() as conn:
        with pytest.raises(DBAPIError):
            conn.execute(
                text("UPDATE processed_events SET outcome = 'REJECTED' WHERE event_id = :event_id"),
                {"event_id": event_id},
            )

    with app_sync_engine.connect() as conn:
        with pytest.raises(DBAPIError):
            conn.execute(
                text("DELETE FROM processed_events WHERE event_id = :event_id"),
                {"event_id": event_id},
            )

    # 9. invoice_app CAN insert into payment_allocations
    with app_sync_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO payment_allocations (allocation_id, invoice_id, payment_id, event_id, allocated_amount)
                VALUES (:alloc_id, :inv_id, :pay_id, :event_id, 100.0)
                """
            ),
            {"alloc_id": alloc_id, "inv_id": inv_id, "pay_id": pay_id, "event_id": event_id},
        )

    # 10. invoice_app CANNOT UPDATE or DELETE payment_allocations
    with app_sync_engine.connect() as conn:
        with pytest.raises(DBAPIError):
            conn.execute(
                text("UPDATE payment_allocations SET allocated_amount = 200.0 WHERE allocation_id = :alloc_id"),
                {"alloc_id": alloc_id},
            )

    with app_sync_engine.connect() as conn:
        with pytest.raises(DBAPIError):
            conn.execute(
                text("DELETE FROM payment_allocations WHERE allocation_id = :alloc_id"),
                {"alloc_id": alloc_id},
            )


def test_database_check_constraints(app_sync_engine):
    """Verify that PostgreSQL CHECK constraints enforce business rules directly at database level."""
    biz_id = str(uuid4())
    inv_id = str(uuid4())

    with app_sync_engine.connect() as conn:
        # Negative amount_paid rejected
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    """
                    INSERT INTO invoices (invoice_id, business_id, customer_id, due_date, currency, status, amount_paid)
                    VALUES (:inv_id, :biz_id, :cust_id, '2026-12-01', 'USD', 'DRAFT', -10.0)
                    """
                ),
                {"inv_id": str(uuid4()), "biz_id": biz_id, "cust_id": str(uuid4())},
            )
        conn.rollback()

        # amount_paid > total_amount rejected
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    """
                    INSERT INTO invoices (invoice_id, business_id, customer_id, due_date, currency, status, total_amount, amount_paid)
                    VALUES (:inv_id, :biz_id, :cust_id, '2026-12-01', 'USD', 'DRAFT', 100.0, 150.0)
                    """
                ),
                {"inv_id": str(uuid4()), "biz_id": biz_id, "cust_id": str(uuid4())},
            )
        conn.rollback()

        # Non-draft status without required snapshots and invoice_number rejected
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    """
                    INSERT INTO invoices (invoice_id, business_id, customer_id, due_date, currency, status)
                    VALUES (:inv_id, :biz_id, :cust_id, '2026-12-01', 'USD', 'ISSUED')
                    """
                ),
                {"inv_id": str(uuid4()), "biz_id": biz_id, "cust_id": str(uuid4())},
            )
        conn.rollback()

        # VOID status without void_reason rejected
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    """
                    INSERT INTO invoices (invoice_id, business_id, customer_id, due_date, currency, status)
                    VALUES (:inv_id, :biz_id, :cust_id, '2026-12-01', 'USD', 'VOID')
                    """
                ),
                {"inv_id": str(uuid4()), "biz_id": biz_id, "cust_id": str(uuid4())},
            )
        conn.rollback()


def test_migration_downgrade_and_roundtrip(owner_engine):
    """Proof of migration downgrade and clean round-trip upgrade."""
    cfg = get_alembic_config()

    # 1. Downgrade to base
    command.downgrade(cfg, "base")

    with owner_engine.connect() as conn:
        tables = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name;"
            )
        ).fetchall()
        table_names = [t[0] for t in tables]
        # Only alembic_version remains
        assert "invoices" not in table_names
        assert "audit_log" not in table_names
        assert "payment_allocations" not in table_names

    # 2. Upgrade back to head
    command.upgrade(cfg, "head")

    with owner_engine.connect() as conn:
        tables = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name;"
            )
        ).fetchall()
        table_names = [t[0] for t in tables]
        assert "invoices" in table_names
        assert "audit_log" in table_names
        assert "payment_allocations" in table_names
