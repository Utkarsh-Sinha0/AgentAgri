"""Seed a mock farmer bound to a real Telegram numeric user ID.

Reuses app.services.demo_seed.seed_demo_memory_palace, which is idempotent
(upsert) so running this multiple times is safe. Defaults to the developer's
Telegram numeric ID; override with --telegram-id <numeric_id>.

Usage:
    python scripts/seed_mock_farmer.py
    python scripts/seed_mock_farmer.py --telegram-id 1345155802
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

from app.database import async_session_factory, init_db
from app.services.demo_seed import seed_demo_memory_palace


async def main(telegram_user_id: str) -> None:
    logger.info(f"Initializing database for telegram_user_id={telegram_user_id}")
    await init_db()

    async with async_session_factory() as db:
        summary = await seed_demo_memory_palace(db, telegram_user_id=telegram_user_id)

    logger.info("Mock farmer seeded:")
    for key, value in summary.items():
        logger.info(f"  {key}: {value}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--telegram-id",
        default="1345155802",
        help="Telegram numeric user ID to bind the mock farmer to.",
    )
    args = parser.parse_args()
    asyncio.run(main(args.telegram_id))
