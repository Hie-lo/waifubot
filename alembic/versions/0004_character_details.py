"""add characters.details_json (structured character traits for edit + combine)

Revision ID: 0004
Revises: 0003
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("characters") as batch:
        batch.add_column(sa.Column("details_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("characters") as batch:
        batch.drop_column("details_json")
