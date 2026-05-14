from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import Connection, make_url

from alembic import context

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.database import Base  # noqa: E402

import app.models  # noqa: E402,F401
import app.models_memory  # noqa: E402,F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_migration_url(url: str) -> str:
    """Alembic runs synchronously; map async app URLs to sync DBAPI drivers."""
    parsed = make_url(url)
    if parsed.drivername == "sqlite+aiosqlite":
        return str(parsed.set(drivername="sqlite"))
    if parsed.drivername == "postgresql+asyncpg":
        return str(parsed.set(drivername="postgresql+psycopg2"))
    return url


def run_migrations_offline() -> None:
    """Run migrations in offline mode."""
    context.configure(
        url=_sync_migration_url(settings.database_url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def _run_migrations_with_connection(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in online mode."""
    existing_connection = config.attributes.get("connection")
    if existing_connection is not None:
        _run_migrations_with_connection(existing_connection)
        return

    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _sync_migration_url(settings.database_url)
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        _run_migrations_with_connection(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
