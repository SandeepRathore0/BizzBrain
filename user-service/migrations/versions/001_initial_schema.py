"""001_initial_schema

Revision ID: 001_initial_schema
Revises: 
Create Date: 2026-11-07 15:45:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '001_initial_schema'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. users table
    op.create_table(
        'users',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('full_name', sa.String(length=100), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('email_normalized', sa.String(length=255), nullable=False),
        sa.Column('password_hash', sa.Text(), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('email_verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('failed_login_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('user_id', name='pk_users'),
    )
    op.create_index('ix_users_email_normalized', 'users', ['email_normalized'], unique=True)

    # 2. businesses table
    op.create_table(
        'businesses',
        sa.Column('business_id', sa.UUID(), nullable=False),
        sa.Column('business_name', sa.String(length=150), nullable=False),
        sa.Column('base_currency', sa.String(length=3), nullable=False),
        sa.Column('timezone', sa.String(length=50), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('business_id', name='pk_businesses'),
    )

    # 3. business_members table
    op.create_table(
        'business_members',
        sa.Column('membership_id', sa.UUID(), nullable=False),
        sa.Column('business_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.business_id'], name='fk_business_members_business_id', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.user_id'], name='fk_business_members_user_id', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('membership_id', name='pk_business_members'),
        sa.UniqueConstraint('business_id', 'user_id', name='uq_business_member_business_user'),
        sa.CheckConstraint("role IN ('OWNER', 'ADMIN', 'EMPLOYEE')", name='check_business_member_role'),
    )
    op.create_index('ix_business_members_business_id', 'business_members', ['business_id'])
    op.create_index('ix_business_members_user_id', 'business_members', ['user_id'])

    # 4. refresh_tokens table
    op.create_table(
        'refresh_tokens',
        sa.Column('token_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('family_id', sa.UUID(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('user_agent', sa.String(length=500), nullable=True),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('issued_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('replaced_by', sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.user_id'], name='fk_refresh_tokens_user_id', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('token_id', name='pk_refresh_tokens'),
    )
    op.create_index('ix_refresh_tokens_token_hash', 'refresh_tokens', ['token_hash'], unique=True)
    op.create_index('ix_refresh_tokens_user_id', 'refresh_tokens', ['user_id'])
    op.create_index('ix_refresh_tokens_family_id', 'refresh_tokens', ['family_id'])

    # 5. email_tokens table
    op.create_table(
        'email_tokens',
        sa.Column('token_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('purpose', sa.String(length=50), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.user_id'], name='fk_email_tokens_user_id', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('token_id', name='pk_email_tokens'),
    )
    op.create_index('ix_email_tokens_token_hash', 'email_tokens', ['token_hash'], unique=True)
    op.create_index('ix_email_tokens_user_id', 'email_tokens', ['user_id'])

    # 6. audit_log table
    op.create_table(
        'audit_log',
        sa.Column('audit_id', sa.UUID(), nullable=False),
        sa.Column('business_id', sa.UUID(), nullable=True),
        sa.Column('actor_type', sa.String(length=50), nullable=False),
        sa.Column('actor_id', sa.String(length=255), nullable=False),
        sa.Column('action', sa.String(length=100), nullable=False),
        sa.Column('entity_type', sa.String(length=100), nullable=False),
        sa.Column('entity_id', sa.UUID(), nullable=True),
        sa.Column('before', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('after', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('request_id', sa.String(length=100), nullable=True),
        sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('audit_id', name='pk_audit_log'),
    )
    op.create_index('ix_audit_log_business_id', 'audit_log', ['business_id'])
    op.create_index('ix_audit_log_occurred_at', 'audit_log', ['occurred_at'])
    op.create_index('ix_audit_log_entity', 'audit_log', ['entity_type', 'entity_id'])


def downgrade() -> None:
    op.drop_table('audit_log')
    op.drop_table('email_tokens')
    op.drop_table('refresh_tokens')
    op.drop_table('business_members')
    op.drop_table('businesses')
    op.drop_table('users')
