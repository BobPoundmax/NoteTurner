"""Google Drive vector store: pgvector extension and doc_chunks table

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-03

"""
from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 1536


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "doc_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("external_id", sa.String(length=100), nullable=False),
        sa.Column("record_type", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column(
            "is_financial",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_doc_chunks_source"), "doc_chunks", ["source"])
    op.create_index(op.f("ix_doc_chunks_external_id"), "doc_chunks", ["external_id"])
    op.create_index(op.f("ix_doc_chunks_is_financial"), "doc_chunks", ["is_financial"])
    op.create_index(
        "ix_doc_chunks_embedding",
        "doc_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_doc_chunks_embedding", table_name="doc_chunks")
    op.drop_index(op.f("ix_doc_chunks_is_financial"), table_name="doc_chunks")
    op.drop_index(op.f("ix_doc_chunks_external_id"), table_name="doc_chunks")
    op.drop_index(op.f("ix_doc_chunks_source"), table_name="doc_chunks")
    op.drop_table("doc_chunks")
