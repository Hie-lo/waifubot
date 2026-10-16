"""add images.is_nsfw (user 2026-10-16: NSFW flag per image -> spoiler on post)

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("images") as batch:
        batch.add_column(
            sa.Column("is_nsfw", sa.Boolean(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("images") as batch:
        batch.drop_column("is_nsfw")
