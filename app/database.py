"""
AgriMesh V4.0 — Async Database Engine
Supports SQLite (dev) and PostgreSQL (prod) via SQLAlchemy 2.0 async.
"""
from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from alembic.config import Config
from alembic.script import ScriptDirectory
from app.config import settings

# Detect SQLite vs PostgreSQL from the connection string
_use_sqlite = settings.database_url.startswith("sqlite")

engine = create_async_engine(
    settings.database_url,
    echo=False,
    # SQLite needs this for async access across threads
    connect_args={"check_same_thread": False} if _use_sqlite else {},
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    """FastAPI dependency — yields an async session."""
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """Initialize the database or fail fast when migrations have not run."""
    import app.models  # noqa: F401 — register core tables first
    import app.models_memory  # noqa: F401 — register memory + evidence tables

    should_autocreate = (
        settings.app_env in {"development", "test"}
        and os.getenv("AGRIMESH_DEV_AUTOCREATE", "1") == "1"
    )
    async with engine.begin() as conn:
        if should_autocreate:
            await conn.run_sync(Base.metadata.create_all)
        else:
            await _assert_database_at_alembic_head(conn)

        if _use_sqlite and os.getenv("AGRIMESH_DEV_AUTOREPAIR_SQLITE", "0") == "1":
            await _sync_sqlite_columns(conn)


async def _assert_database_at_alembic_head(conn) -> None:
    """Require deployed databases to be managed by Alembic and at head."""
    try:
        result = await conn.execute(text("SELECT version_num FROM alembic_version"))
        current_versions = {row[0] for row in result.fetchall()}
    except Exception as exc:
        logger.bind(error=str(exc)).warning("Alembic version check failed")
        raise RuntimeError("Database is missing alembic_version; run `make migrate`") from exc

    try:
        alembic_cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
        script = ScriptDirectory.from_config(alembic_cfg)
        heads = set(script.get_heads())
    except Exception as exc:
        logger.bind(error=str(exc)).warning("Alembic head lookup failed")
        raise RuntimeError("Unable to determine Alembic migration head") from exc

    if not current_versions or current_versions != heads:
        raise RuntimeError(
            "Database schema is not at Alembic head; "
            f"current={sorted(current_versions) or ['<missing>']} head={sorted(heads)}"
        )


async def _sync_sqlite_columns(conn) -> None:
    """Add missing columns for local SQLite databases."""
    try:
        dialect = conn.dialect
        for table in Base.metadata.sorted_tables:
            table_name = table.name.replace('"', '""')
            result = await conn.execute(text(f'PRAGMA table_info("{table_name}")'))
            existing_columns = {row[1] for row in result.fetchall()}
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                column_name = column.name.replace('"', '""')
                column_type = column.type.compile(dialect=dialect)
                with suppress(Exception):
                    await conn.execute(
                        text(f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {column_type}')
                    )
    except Exception as exc:
        logger.bind(error=str(exc)).warning("SQLite additive schema sync skipped")
