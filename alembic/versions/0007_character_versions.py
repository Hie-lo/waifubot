"""Alembic migration 0007: character versions table with edit_of backlink.

Revision ID: 0007_character_versions
Revises: 0006_image_provenance
Create Date: 2026-10-18 00:07:30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_character_versions"
down_revision: str | None = "0006_image_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "characters",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("character_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("edit_of", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("appearance_facts", sa.Text(), nullable=False),
        sa.Column("traits_json", sa.Text(), nullable=True),
        sa.Column("extras", sa.Text(), nullable=True),
        sa.Column("mode", sa.String(length=32), server_default="known", nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_characters"),
    )
    op.create_index("ix_characters_character_id", "characters", ["character_id"], unique=False)
    op.create_index("ix_characters_is_current", "characters", ["is_current"], unique=False)
    op.create_index("ix_characters_created_by", "characters", ["created_by"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_characters_created_by", table_name="characters")
    op.drop_index("ix_characters_is_current", table_name="characters")
    op.drop_index("ix_characters_character_id", table_name="characters")
    op.drop_table("characters")
