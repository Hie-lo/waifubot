"""Alembic migration 0014: character_personas.

Revision ID: 0014_personas
Revises: 0013_character_ownership
Create Date: 2026-10-18 14:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_personas"
down_revision: str | None = "0013_character_ownership"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "character_personas",
        sa.Column("character_id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("fields_json", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("card", sa.Text(), nullable=False),
        sa.Column("card_hash", sa.String(length=64), nullable=False),
        sa.Column("prev_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("character_personas")
