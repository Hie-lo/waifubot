"""Alembic migration 0005: job lease table.

Revision ID: 0005_job_lease
Revises: None
Create Date: 2026-10-17 22:25:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_job_lease"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("requester_user_id", sa.BigInteger(), nullable=False),
        sa.Column("admin_id", sa.BigInteger(), nullable=False),
        sa.Column("job_scope", sa.String(length=16), server_default="staff", nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("negative_prompt", sa.Text(), server_default="", nullable=False),
        sa.Column("target_count", sa.Integer(), server_default="4", nullable=False),
        sa.Column("needed", sa.Integer(), server_default="4", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("run_no", sa.Integer(), server_default="1", nullable=False),
        sa.Column("rounds_done", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_rounds", sa.Integer(), server_default="6", nullable=False),
        sa.Column("zero_progress_streak", sa.Integer(), server_default="0", nullable=False),
        sa.Column("zero_progress_limit", sa.Integer(), server_default="3", nullable=False),
        sa.Column("surplus_dropped", sa.Integer(), server_default="0", nullable=False),
        sa.Column("attempts_done", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("next_attempt_at", sa.Float(), nullable=True),
        sa.Column("queue_priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.Float(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
    )
    op.create_index("ix_jobs_job_id", "jobs", ["job_id"], unique=True)
    op.create_index("ix_jobs_requester_user_id", "jobs", ["requester_user_id"], unique=False)
    op.create_index("ix_jobs_admin_id", "jobs", ["admin_id"], unique=False)
    op.create_index("ix_jobs_status", "jobs", ["status"], unique=False)
    op.create_index("ix_jobs_next_attempt_at", "jobs", ["next_attempt_at"], unique=False)
    op.create_index("ix_jobs_queue_priority", "jobs", ["queue_priority"], unique=False)
    op.create_index("ix_jobs_lease_owner", "jobs", ["lease_owner"], unique=False)
    op.create_index("ix_jobs_lease_expires_at", "jobs", ["lease_expires_at"], unique=False)
    op.create_index("ix_jobs_created_at", "jobs", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_jobs_created_at", table_name="jobs")
    op.drop_index("ix_jobs_lease_expires_at", table_name="jobs")
    op.drop_index("ix_jobs_lease_owner", table_name="jobs")
    op.drop_index("ix_jobs_queue_priority", table_name="jobs")
    op.drop_index("ix_jobs_next_attempt_at", table_name="jobs")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_index("ix_jobs_admin_id", table_name="jobs")
    op.drop_index("ix_jobs_requester_user_id", table_name="jobs")
    op.drop_index("ix_jobs_job_id", table_name="jobs")
    op.drop_table("jobs")
