"""
AgriMesh V4.0 — Test Fixtures & Configuration
Each test function gets a fresh SQLite database via create_all/drop_all.
"""
import asyncio
import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./data/test_agrimesh.db"
os.environ["OLLAMA_MODEL"] = "gemma4:e2b"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["USE_GRAMMAR_DECODING"] = "0"
os.environ.setdefault("AGRIMESH_REQUIRE_API_KEY", "false")
os.environ.setdefault("APP_ENV", "test")


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    """Fresh DB per test function."""
    import app.models  # noqa: F401
    import app.models_memory  # noqa: F401
    from app.database import Base, engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session():
    from app.database import async_session_factory
    async with async_session_factory() as session:
        yield session
