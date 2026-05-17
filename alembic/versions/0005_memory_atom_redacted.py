"""add memory atom redacted flag

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-17
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("memory_atoms") as batch_op:
        batch_op.add_column(sa.Column("redacted", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_memory_atoms_redacted", "memory_atoms", ["redacted"])


def downgrade() -> None:
    op.drop_index("ix_memory_atoms_redacted", table_name="memory_atoms")
    with op.batch_alter_table("memory_atoms") as batch_op:
        batch_op.drop_column("redacted")
