"""Alembic migration 0015: chat_sessions and chat_memory.

Revision ID: 0015_chat
Revises: 0014_personas
Create Date: 2026-10-18 14:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015_chat"
down_revision: str | None = "0014_personas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create chat_sessions table
    op.create_table(
        "chat_sessions",
        sa.Column("session_id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("character_id", sa.String(length=64), nullable=False),
        sa.Column("last_at", sa.Float(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
    )
    op.create_index("ix_chat_sessions_user_id", "chat_sessions", ["user_id"], unique=False)
    op.create_index(
        "ix_chat_sessions_character_id", "chat_sessions", ["character_id"], unique=False
    )

    # 2. Create chat_memory table
    op.create_table(
        "chat_memory",
        sa.Column(
            "session_id",
            sa.String(length=64),
            sa.ForeignKey("chat_sessions.session_id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("window_json", sa.Text(), server_default="[]", nullable=False),
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
        sa.Column("facts_json", sa.Text(), server_default="[]", nullable=False),
        sa.Column("mem_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("turn_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("since_compaction", sa.Integer(), server_default="0", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("chat_memory")
    op.drop_index("ix_chat_sessions_character_id", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_user_id", table_name="chat_sessions")
    op.drop_table("chat_sessions")
