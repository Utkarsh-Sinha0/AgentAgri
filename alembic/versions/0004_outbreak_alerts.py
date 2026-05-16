"""0004_outbreak_alerts

Add outbreak-warning columns to ``alert_clusters`` so the 10%-threshold
pest/disease early-warning system can persist and dedupe alerts.

New columns (all nullable / default-safe):

- ``kind``                  pattern | outbreak  (default pattern → preserves legacy rows)
- ``scope``                 village | tehsil | district
- ``pest_or_disease``       canonical label of the threat (used to dedupe)
- ``reporting_farmer_ids``  farmers whose reports tripped the threshold
- ``notified_farmer_ids``   farmers who received the push warning
- ``consumed_farmer_ids``   farmers for whom the next-reply prepend has fired
- ``expires_at``            after which the alert is no longer surfaced

SQLite-safe via ``batch_alter_table``.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-16
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("alert_clusters", recreate="auto") as batch_op:
        batch_op.add_column(sa.Column("kind", sa.String(length=20), nullable=True, server_default="pattern"))
        batch_op.add_column(sa.Column("scope", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("pest_or_disease", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("reporting_farmer_ids", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("notified_farmer_ids", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("consumed_farmer_ids", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("expires_at", sa.DateTime(), nullable=True))
        batch_op.create_index("ix_alert_clusters_kind_crop_village", ["kind", "crop_name", "village"])
        batch_op.create_index("ix_alert_clusters_kind_expires", ["kind", "expires_at"])


def downgrade() -> None:
    with op.batch_alter_table("alert_clusters", recreate="auto") as batch_op:
        batch_op.drop_index("ix_alert_clusters_kind_expires")
        batch_op.drop_index("ix_alert_clusters_kind_crop_village")
        batch_op.drop_column("expires_at")
        batch_op.drop_column("consumed_farmer_ids")
        batch_op.drop_column("notified_farmer_ids")
        batch_op.drop_column("reporting_farmer_ids")
        batch_op.drop_column("pest_or_disease")
        batch_op.drop_column("scope")
        batch_op.drop_column("kind")
