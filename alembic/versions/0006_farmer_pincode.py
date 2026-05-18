"""add farmer pincode

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-18
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("farmers") as batch_op:
        batch_op.add_column(sa.Column("pincode", sa.String(6), nullable=True))
    op.create_index("ix_farmers_pincode", "farmers", ["pincode"])


def downgrade() -> None:
    op.drop_index("ix_farmers_pincode", table_name="farmers")
    with op.batch_alter_table("farmers") as batch_op:
        batch_op.drop_column("pincode")
