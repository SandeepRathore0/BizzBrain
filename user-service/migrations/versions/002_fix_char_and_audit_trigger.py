"""002_fix_char_and_audit_trigger

Revision ID: 002_fix_char_and_audit_trigger
Revises: 001_initial_schema
Create Date: 2026-11-07 17:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '002_fix_char_and_audit_trigger'
down_revision: Union[str, None] = '001_initial_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Alter businesses.base_currency to CHAR(3) and add uppercase regex check constraint
    op.alter_column(
        'businesses',
        'base_currency',
        existing_type=sa.String(length=3),
        type_=sa.CHAR(length=3),
        existing_nullable=False,
    )
    op.create_check_constraint(
        'check_business_base_currency_format',
        'businesses',
        "base_currency ~ '^[A-Z]{3}$'",
    )

    # 2. Alter refresh_tokens.token_hash to CHAR(64)
    op.alter_column(
        'refresh_tokens',
        'token_hash',
        existing_type=sa.String(length=64),
        type_=sa.CHAR(length=64),
        existing_nullable=False,
    )

    # 3. Alter email_tokens.token_hash to CHAR(64)
    op.alter_column(
        'email_tokens',
        'token_hash',
        existing_type=sa.String(length=64),
        type_=sa.CHAR(length=64),
        existing_nullable=False,
    )

    # 4. Defense-in-depth: audit_log triggers to block UPDATE, DELETE, and TRUNCATE even for table owners
    op.execute("""
    CREATE OR REPLACE FUNCTION trg_audit_log_prevent_modification()
    RETURNS TRIGGER AS $$
    BEGIN
        RAISE EXCEPTION 'audit_log is append-only: UPDATE, DELETE and TRUNCATE operations are strictly forbidden.';
    END;
    $$ LANGUAGE plpgsql;
    """)

    op.execute("""
    CREATE TRIGGER audit_log_prevent_modification_trg
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW
    EXECUTE FUNCTION trg_audit_log_prevent_modification();
    """)

    op.execute("""
    CREATE TRIGGER audit_log_prevent_truncate_trg
    BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT
    EXECUTE FUNCTION trg_audit_log_prevent_modification();
    """)

    # 5. Role permissions: revoke modification on audit_log
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON TABLE audit_log FROM PUBLIC;")
    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'bizbrain_app') THEN
            REVOKE ALL ON TABLE audit_log FROM bizbrain_app;
            GRANT SELECT, INSERT ON TABLE audit_log TO bizbrain_app;
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE users TO bizbrain_app;
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE businesses TO bizbrain_app;
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE business_members TO bizbrain_app;
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE refresh_tokens TO bizbrain_app;
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE email_tokens TO bizbrain_app;
        END IF;
    END
    $$;
    """)


def downgrade() -> None:
    # 1. Drop audit_log triggers and function
    op.execute("DROP TRIGGER IF EXISTS audit_log_prevent_truncate_trg ON audit_log;")
    op.execute("DROP TRIGGER IF EXISTS audit_log_prevent_modification_trg ON audit_log;")
    op.execute("DROP FUNCTION IF EXISTS trg_audit_log_prevent_modification();")

    # 2. Revert email_tokens.token_hash to String(64)
    op.alter_column(
        'email_tokens',
        'token_hash',
        existing_type=sa.CHAR(length=64),
        type_=sa.String(length=64),
        existing_nullable=False,
    )

    # 3. Revert refresh_tokens.token_hash to String(64)
    op.alter_column(
        'refresh_tokens',
        'token_hash',
        existing_type=sa.CHAR(length=64),
        type_=sa.String(length=64),
        existing_nullable=False,
    )

    # 4. Drop check constraint and revert businesses.base_currency to String(3)
    op.drop_constraint('check_business_base_currency_format', 'businesses', type_='check')
    op.alter_column(
        'businesses',
        'base_currency',
        existing_type=sa.CHAR(length=3),
        type_=sa.String(length=3),
        existing_nullable=False,
    )
