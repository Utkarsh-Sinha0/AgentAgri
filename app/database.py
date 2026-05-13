"""
AgriMesh V4.0 — Async Database Engine
Supports SQLite (dev) and PostgreSQL (prod) via SQLAlchemy 2.0 async.
"""
from __future__ import annotations

from contextlib import suppress

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

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
    """Create all tables and repair additive SQLite schema drift."""
    import app.models  # noqa: F401 — register core tables first
    import app.models_memory  # noqa: F401 — register memory + evidence tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


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
    except Exception:
        pass  # Best-effort sync
