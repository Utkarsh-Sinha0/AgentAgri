"""add pincode column to alert_clusters

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-18
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("alert_clusters") as batch_op:
        batch_op.add_column(sa.Column("pincode", sa.String(6), nullable=True))
    op.create_index("ix_alert_clusters_pincode", "alert_clusters", ["pincode"])


def downgrade() -> None:
    op.drop_index("ix_alert_clusters_pincode", table_name="alert_clusters")
    with op.batch_alter_table("alert_clusters") as batch_op:
        batch_op.drop_column("pincode")
