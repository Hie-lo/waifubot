"""Alembic migration 0012: channels, channel_topics, and schedule channel FK.

Revision ID: 0012_channels
Revises: 0011_plans_and_usage
Create Date: 2026-10-18 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_channels"
down_revision: str | None = "0011_plans_and_usage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create channels table
    channels_table = op.create_table(
        "channels",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tg_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=32), server_default="channel", nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("alias", sa.String(length=64), nullable=True),
        sa.Column("owner_user_id", sa.BigInteger(), nullable=False),
        sa.Column("scope", sa.String(length=16), server_default="private", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("bot_rights_json", sa.Text(), server_default="{}", nullable=False),
        sa.Column("verified_at", sa.Float(), nullable=True),
        sa.Column("added_by", sa.BigInteger(), nullable=False),
        sa.Column(
            "adder_role_in_chat",
            sa.String(length=32),
            server_default="administrator",
            nullable=False,
        ),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_channels"),
    )
    op.create_index("ix_channels_tg_chat_id", "channels", ["tg_chat_id"], unique=True)
    op.create_index("ix_channels_owner_user_id", "channels", ["owner_user_id"], unique=False)
    op.create_index("ix_channels_status", "channels", ["status"], unique=False)

    # 2. Create channel_topics table
    op.create_table(
        "channel_topics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id"], ["channels.id"], ondelete="CASCADE", name="fk_channel_topics_channel_id"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_channel_topics"),
    )
    op.create_index("ix_channel_topics_channel_id", "channel_topics", ["channel_id"], unique=False)

    # 3. Add telegram_file_id column to images table (R42)
    with op.batch_alter_table("images") as batch_op:
        batch_op.add_column(sa.Column("telegram_file_id", sa.String(length=128), nullable=True))

    # 4. Migrate old schedule channel ids as legacy_unverified (R58)
    bind = op.get_bind()
    schedules_table = sa.table(
        "schedules",
        sa.column("id", sa.Integer),
        sa.column("channel_id", sa.BigInteger),
    )

    # Select distinct channel ids from schedules
    try:
        raw_rows = bind.execute(sa.select(schedules_table.c.channel_id).distinct()).fetchall()
        for r in raw_rows:
            raw_channel_id = r[0]
            if raw_channel_id is not None:
                # Insert legacy channel
                res = bind.execute(
                    channels_table.insert().values(
                        tg_chat_id=raw_channel_id,
                        kind="channel",
                        title=f"Legacy Channel {raw_channel_id}",
                        owner_user_id=0,
                        scope="shared",
                        status="legacy_unverified",
                        bot_rights_json="{}",
                        added_by=0,
                        adder_role_in_chat="creator",
                        created_at=0.0,
                        updated_at=0.0,
                    )
                )
                new_pk = res.inserted_primary_key[0]
                bind.execute(
                    schedules_table.update()
                    .where(schedules_table.c.channel_id == raw_channel_id)
                    .values(channel_id=new_pk)
                )
    except Exception:
        pass


def downgrade() -> None:
    with op.batch_alter_table("images") as batch_op:
        batch_op.drop_column("telegram_file_id")

    op.drop_index("ix_channel_topics_channel_id", table_name="channel_topics")
    op.drop_table("channel_topics")

    op.drop_index("ix_channels_status", table_name="channels")
    op.drop_index("ix_channels_owner_user_id", table_name="channels")
    op.drop_index("ix_channels_tg_chat_id", table_name="channels")
    op.drop_table("channels")
