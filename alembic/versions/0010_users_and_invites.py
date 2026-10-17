"""Alembic migration 0010: users and admin_invites tables.

Revision ID: 0010_users_and_invites
Revises: 0009_audit_and_admin
Create Date: 2026-10-18 00:25:30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0010_users_and_invites"
down_revision: str | None = "0009_audit_and_admin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("locale", sa.String(length=8), server_default="fa", nullable=False),
        sa.Column("first_seen", sa.Float(), nullable=False),
        sa.Column("last_seen", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("block_reason", sa.String(length=255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("joined_via", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )
    op.create_index("ix_users_telegram_id", "users", ["telegram_id"], unique=True)
    op.create_index("ix_users_status", "users", ["status"], unique=False)

    op.create_table(
        "admin_invites",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=32), server_default="admin", nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("used_by", sa.BigInteger(), nullable=True),
        sa.Column("used_at", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_admin_invites"),
    )
    op.create_index("ix_admin_invites_token_hash", "admin_invites", ["token_hash"], unique=True)
    op.create_index("ix_admin_invites_expires_at", "admin_invites", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_admin_invites_expires_at", table_name="admin_invites")
    op.drop_index("ix_admin_invites_token_hash", table_name="admin_invites")
    op.drop_table("admin_invites")

    op.drop_index("ix_users_status", table_name="users")
    op.drop_index("ix_users_telegram_id", table_name="users")
    op.drop_table("users")
