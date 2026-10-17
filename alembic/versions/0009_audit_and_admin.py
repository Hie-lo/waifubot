"""Alembic migration 0009: audit_log and admins tables.

Revision ID: 0009_audit_and_admin
Revises: 0008_schedules
Create Date: 2026-10-18 00:25:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0009_audit_and_admin"
down_revision: str | None = "0008_schedules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("at", sa.Float(), nullable=False),
        sa.Column("admin_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=128), nullable=False),
        sa.Column("old_val", sa.Text(), nullable=True),
        sa.Column("new_val", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log"),
    )
    op.create_index("ix_audit_log_at", "audit_log", ["at"], unique=False)
    op.create_index("ix_audit_log_admin_id", "audit_log", ["admin_id"], unique=False)

    op.create_table(
        "admins",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=32), server_default="admin", nullable=False),
        sa.Column("added_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("removed_at", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_admins"),
    )
    op.create_index("ix_admins_telegram_id", "admins", ["telegram_id"], unique=True)
    op.create_index("ix_admins_removed_at", "admins", ["removed_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_admins_removed_at", table_name="admins")
    op.drop_index("ix_admins_telegram_id", table_name="admins")
    op.drop_table("admins")

    op.drop_index("ix_audit_log_admin_id", table_name="audit_log")
    op.drop_index("ix_audit_log_at", table_name="audit_log")
    op.drop_table("audit_log")
