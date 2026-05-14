from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from alembic import command
from alembic.config import Config

ALEMBIC_CLI_AVAILABLE = bool(shutil.which("alembic")) or (
    Path(sys.executable).parent / "alembic.exe"
).exists()


def _schema_signature(connection):
    inspector = inspect(connection)
    rows = set()
    for table_name in inspector.get_table_names():
        if table_name == "alembic_version":
            continue
        for column in inspector.get_columns(table_name):
            rows.add(
                (
                    table_name,
                    column["name"],
                    type(column["type"]).__name__,
                    column["nullable"],
                    bool(column.get("primary_key")),
                )
            )
    return rows


@pytest.mark.skipif(not ALEMBIC_CLI_AVAILABLE, reason="alembic CLI unavailable")
def test_alembic_baseline_matches_create_all_schema():
    import app.models  # noqa: F401
    import app.models_memory  # noqa: F401
    from app.database import Base

    alembic_engine = create_engine("sqlite:///:memory:")
    metadata_engine = create_engine("sqlite:///:memory:")
    alembic_cfg = Config("alembic.ini")

    try:
        with alembic_engine.begin() as alembic_conn:
            alembic_cfg.attributes["connection"] = alembic_conn
            command.upgrade(alembic_cfg, "head")
            alembic_schema = _schema_signature(alembic_conn)

        with metadata_engine.begin() as metadata_conn:
            Base.metadata.create_all(metadata_conn)
            metadata_schema = _schema_signature(metadata_conn)
    finally:
        alembic_engine.dispose()
        metadata_engine.dispose()

    assert alembic_schema == metadata_schema
