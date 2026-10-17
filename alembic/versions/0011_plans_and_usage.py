"""Alembic migration 0011: plans, usage_counters, and user plan fields.

Revision ID: 0011_plans_and_usage
Revises: 0010_users_and_invites
Create Date: 2026-10-18 01:00:00
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_plans_and_usage"
down_revision: str | None = "0010_users_and_invites"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create plans table
    plans_table = op.create_table(
        "plans",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("limits_json", sa.Text(), nullable=False),
        sa.Column("is_builtin", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_plans"),
    )

    # 2. Create usage_counters table
    op.create_table(
        "usage_counters",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("period_key", sa.String(length=32), nullable=False),
        sa.Column("images", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("jobs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("chat_messages", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("tokens_in", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("tokens_out", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_usage_counters"),
    )
    op.create_index("ix_usage_counters_user_id", "usage_counters", ["user_id"], unique=False)
    op.create_index("ix_usage_counters_period_key", "usage_counters", ["period_key"], unique=False)
    op.create_index(
        "ix_usage_counters_user_period", "usage_counters", ["user_id", "period_key"], unique=True
    )

    # 3. Add plan columns to users
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column("plan_id", sa.String(length=32), server_default="free", nullable=False)
        )
        batch_op.add_column(sa.Column("plan_expires_at", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("limit_overrides_json", sa.Text(), nullable=True))

    # 4. Seed built-in plans: free and staff
    free_limits = {
        "max_owned_characters": 3,
        "daily_image_budget": 8,
        "max_images_per_job": 4,
        "max_concurrent_jobs": 1,
        "max_stored_images": 100,
        "daily_chat_messages": 60,
        "chat_memory_tokens": 500,
        "max_channels": 0,
        "queue_priority": 0,
    }
    staff_limits = {
        "max_owned_characters": None,
        "daily_image_budget": None,
        "max_images_per_job": 16,
        "max_concurrent_jobs": None,
        "max_stored_images": None,
        "daily_chat_messages": None,
        "chat_memory_tokens": 1500,
        "max_channels": None,
        "queue_priority": 10,
    }

    op.bulk_insert(
        plans_table,
        [
            {
                "id": "free",
                "name": "Free",
                "limits_json": json.dumps(free_limits),
                "is_builtin": True,
                "created_by": None,
                "created_at": 0.0,
            },
            {
                "id": "staff",
                "name": "Staff",
                "limits_json": json.dumps(staff_limits),
                "is_builtin": True,
                "created_by": None,
                "created_at": 0.0,
            },
        ],
    )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("limit_overrides_json")
        batch_op.drop_column("plan_expires_at")
        batch_op.drop_column("plan_id")

    op.drop_index("ix_usage_counters_user_period", table_name="usage_counters")
    op.drop_index("ix_usage_counters_period_key", table_name="usage_counters")
    op.drop_index("ix_usage_counters_user_id", table_name="usage_counters")
    op.drop_table("usage_counters")
    op.drop_table("plans")
