"""Alembic migration 0006: image provenance and pHash table.

Revision ID: 0006_image_provenance
Revises: 0005_job_lease
Create Date: 2026-10-18 00:07:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0006_image_provenance"
down_revision: str | None = "0005_job_lease"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "images",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("image_id", sa.String(length=64), nullable=False),
        sa.Column("file_path", sa.String(length=255), nullable=False),
        sa.Column("phash", sa.String(length=32), nullable=True),
        sa.Column("mime_type", sa.String(length=32), server_default="image/png", nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("character_version_id", sa.Integer(), nullable=True),
        sa.Column("final_prompt", sa.Text(), nullable=False),
        sa.Column("negative_prompt", sa.Text(), server_default="", nullable=False),
        sa.Column("seed", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=64), server_default="perchance", nullable=False),
        sa.Column("provider_version", sa.String(length=32), server_default="v1", nullable=False),
        sa.Column("is_enhanced", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_images"),
    )
    op.create_index("ix_images_image_id", "images", ["image_id"], unique=True)
    op.create_index("ix_images_phash", "images", ["phash"], unique=False)
    op.create_index("ix_images_job_id", "images", ["job_id"], unique=False)
    op.create_index("ix_images_character_version_id", "images", ["character_version_id"], unique=False)
    op.create_index("ix_images_created_at", "images", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_images_created_at", table_name="images")
    op.drop_index("ix_images_character_version_id", table_name="images")
    op.drop_index("ix_images_job_id", table_name="images")
    op.drop_index("ix_images_phash", table_name="images")
    op.drop_index("ix_images_image_id", table_name="images")
    op.drop_table("images")
