"""0002_causal_chains

Add ``memory_atoms.causal_predecessor_atom_id`` (self-referential FK) so that
atoms can be linked into a directed causal chain (observation → indicator →
advisory → outcome). Used by SOTA feature M3 (causal chain linking) and by
M2 (outcome-weighted confidence boost) to walk back from an outcome atom to
the advisory/indicator atoms that produced it.

* SQLite-safe: uses ``batch_alter_table`` so the FK is added by rebuilding the
  table rather than via ``ALTER TABLE ADD CONSTRAINT`` (which SQLite does not
  support). PostgreSQL drives the same code path via the batch operation,
  which becomes a plain ``ALTER TABLE`` there.
* Index is added explicitly so causal-chain traversal queries can use it;
  Alembic does not derive an index from the ``index=True`` flag on the model
  during a non-autogenerate upgrade.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-15
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("memory_atoms", recreate="auto") as batch_op:
        batch_op.add_column(
            sa.Column("causal_predecessor_atom_id", sa.String(length=36), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_memory_atoms_causal_predecessor",
            "memory_atoms",
            ["causal_predecessor_atom_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_memory_atoms_causal_predecessor",
            ["causal_predecessor_atom_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("memory_atoms", recreate="auto") as batch_op:
        batch_op.drop_index("ix_memory_atoms_causal_predecessor")
        batch_op.drop_constraint("fk_memory_atoms_causal_predecessor", type_="foreignkey")
        batch_op.drop_column("causal_predecessor_atom_id")
