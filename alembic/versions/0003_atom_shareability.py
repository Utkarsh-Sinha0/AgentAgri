"""0003_atom_shareability

Add ``memory_atoms.is_shareable`` (Boolean, default False, NOT NULL). This
column gates M4 cross-farmer retrieval: only atoms explicitly flagged at
extract time may leave the originating farmer's scope, even within the
``retrieve_similar_farm_context`` atom_type whitelist.

Backfill: atoms whose ``atom_type`` is in the safe whitelist
(disease_observed, pest_detected, advisory_given, outcome_reported) are
marked shareable. Every other atom keeps the safe default of False.

SQLite-safe via ``batch_alter_table``.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-15
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SAFE_TYPES = ("disease_observed", "pest_detected", "advisory_given", "outcome_reported")


def upgrade() -> None:
    with op.batch_alter_table("memory_atoms", recreate="auto") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_shareable",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.create_index(
            "ix_memory_atoms_is_shareable",
            ["is_shareable"],
            unique=False,
        )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE memory_atoms SET is_shareable = :truth "
            "WHERE atom_type IN :safe_types"
        ).bindparams(
            sa.bindparam("safe_types", expanding=True),
        ),
        {"truth": True, "safe_types": list(SAFE_TYPES)},
    )

    # Drop the server_default now that existing rows are populated, so future
    # inserts must opt in explicitly through the application layer.
    with op.batch_alter_table("memory_atoms", recreate="auto") as batch_op:
        batch_op.alter_column("is_shareable", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("memory_atoms", recreate="auto") as batch_op:
        batch_op.drop_index("ix_memory_atoms_is_shareable")
        batch_op.drop_column("is_shareable")
