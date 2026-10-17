"""Alembic migration 0008: schedules table.

Revision ID: 0008_schedules
Revises: 0007_character_versions
Create Date: 2026-10-18 00:20:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0008_schedules"
down_revision: str | None = "0007_character_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "schedules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=True),
        sa.Column("post_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("random_order", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("min_gap_minutes", sa.Integer(), server_default="60", nullable=False),
        sa.Column("max_gap_minutes", sa.Integer(), server_default="180", nullable=False),
        sa.Column("next_at", sa.Float(), nullable=False),
        sa.Column("is_running", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("missed_run_policy", sa.String(length=32), server_default="catch_up_one", nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_schedules"),
    )
    op.create_index("ix_schedules_channel_id", "schedules", ["channel_id"], unique=False)
    op.create_index("ix_schedules_next_at", "schedules", ["next_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_schedules_next_at", table_name="schedules")
    op.drop_index("ix_schedules_channel_id", table_name="schedules")
    op.drop_table("schedules")
