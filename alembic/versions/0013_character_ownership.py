"""Alembic migration 0013: character_ownership, claim_posts, and backfill.

Revision ID: 0013_character_ownership
Revises: 0012_channels
Create Date: 2026-10-18 12:00:00
"""

import time
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_character_ownership"
down_revision: str | None = "0012_channels"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create character_ownership table
    character_ownership_table = op.create_table(
        "character_ownership",
        sa.Column("character_id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("status", sa.String(length=32), server_default="draft", nullable=False),
        sa.Column("owner_user_id", sa.BigInteger(), nullable=True),
        sa.Column("claimable", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("claimed_at", sa.Float(), nullable=True),
        sa.Column("released_at", sa.Float(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )
    op.create_index(
        "ix_character_ownership_owner_user_id",
        "character_ownership",
        ["owner_user_id"],
        unique=False,
    )

    # 2. Create claim_posts table
    op.create_table(
        "claim_posts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("character_id", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_claim_posts_character_id", "claim_posts", ["character_id"], unique=False)

    # 3. Backfill existing characters as 'draft'
    conn = op.get_bind()
    characters_res = conn.execute(
        sa.text("SELECT DISTINCT character_id FROM characters")
    ).fetchall()
    now = time.time()
    for row in characters_res:
        char_id = row[0]
        if char_id:
            conn.execute(
                character_ownership_table.insert().values(
                    character_id=char_id,
                    status="draft",
                    owner_user_id=None,
                    claimable=False,
                    claimed_at=None,
                    released_at=None,
                    created_at=now,
                    updated_at=now,
                )
            )


def downgrade() -> None:
    op.drop_index("ix_claim_posts_character_id", table_name="claim_posts")
    op.drop_table("claim_posts")
    op.drop_index("ix_character_ownership_owner_user_id", table_name="character_ownership")
    op.drop_table("character_ownership")
