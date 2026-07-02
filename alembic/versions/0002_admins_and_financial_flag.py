"""Multi-admins table and financial flag on raw_records

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-02

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admins",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("added_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_admins_telegram_id"), "admins", ["telegram_id"], unique=True)

    op.add_column(
        "raw_records",
        sa.Column(
            "is_financial",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.create_index(op.f("ix_raw_records_is_financial"), "raw_records", ["is_financial"])


def downgrade() -> None:
    op.drop_index(op.f("ix_raw_records_is_financial"), table_name="raw_records")
    op.drop_column("raw_records", "is_financial")
    op.drop_index(op.f("ix_admins_telegram_id"), table_name="admins")
    op.drop_table("admins")
