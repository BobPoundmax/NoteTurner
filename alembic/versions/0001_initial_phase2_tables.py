"""Phase 2 tables: chats, collector_messages, raw_records, sync_runs, query_logs

Revision ID: 0001
Revises:
Create Date: 2026-07-02

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_chats_telegram_chat_id"),
        "chats",
        ["telegram_chat_id"],
        unique=True,
    )

    op.create_table(
        "collector_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=True),
        sa.Column("author_name", sa.String(length=255), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_collector_messages_chat_id"),
        "collector_messages",
        ["chat_id"],
    )

    op.create_table(
        "raw_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("record_type", sa.String(length=50), nullable=False),
        sa.Column("external_id", sa.String(length=100), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_raw_records_source"), "raw_records", ["source"])
    op.create_index(op.f("ix_raw_records_external_id"), "raw_records", ["external_id"])

    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("records_processed", sa.Integer(), nullable=True),
        sa.Column("error_log", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sync_runs_source"), "sync_runs", ["source"])

    op.create_table(
        "query_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_query_logs_telegram_chat_id"),
        "query_logs",
        ["telegram_chat_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_query_logs_telegram_chat_id"), table_name="query_logs")
    op.drop_table("query_logs")
    op.drop_index(op.f("ix_sync_runs_source"), table_name="sync_runs")
    op.drop_table("sync_runs")
    op.drop_index(op.f("ix_raw_records_external_id"), table_name="raw_records")
    op.drop_index(op.f("ix_raw_records_source"), table_name="raw_records")
    op.drop_table("raw_records")
    op.drop_index(op.f("ix_collector_messages_chat_id"), table_name="collector_messages")
    op.drop_table("collector_messages")
    op.drop_index(op.f("ix_chats_telegram_chat_id"), table_name="chats")
    op.drop_table("chats")
