"""Add sync cursors for incremental CRM refresh

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-03

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from noteturner.debug_runtime import agent_debug_log

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # region agent log
    agent_debug_log(
        location="alembic/versions/0004_sync_cursors.py:24",
        message="Migration 0004 upgrade entered",
        data={},
        hypothesis_id="A",
    )
    # endregion
    op.create_table(
        "sync_cursors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("record_type", sa.String(length=50), nullable=False),
        sa.Column("cursor_kind", sa.String(length=20), nullable=False),
        sa.Column("cursor_value", sa.String(length=100), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column(
            "last_records_processed",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "record_type", name="uq_sync_cursors_source_type"),
    )
    # region agent log
    agent_debug_log(
        location="alembic/versions/0004_sync_cursors.py:52",
        message="Migration 0004 create_table finished",
        data={},
        hypothesis_id="A",
    )
    # endregion
    op.create_index(op.f("ix_sync_cursors_source"), "sync_cursors", ["source"], unique=False)
    op.create_index(
        op.f("ix_sync_cursors_record_type"),
        "sync_cursors",
        ["record_type"],
        unique=False,
    )
    # region agent log
    agent_debug_log(
        location="alembic/versions/0004_sync_cursors.py:66",
        message="Migration 0004 upgrade finished",
        data={},
        hypothesis_id="A",
    )
    # endregion


def downgrade() -> None:
    op.drop_index(op.f("ix_sync_cursors_record_type"), table_name="sync_cursors")
    op.drop_index(op.f("ix_sync_cursors_source"), table_name="sync_cursors")
    op.drop_table("sync_cursors")
