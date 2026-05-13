"""
Demo readiness check for local AgriMesh + Telegram workflows.

It verifies the database-backed demo memory palace and reports whether the
configured local Ollama/Gemma model is reachable.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from sqlalchemy import func, select

from app.config import settings
from app.database import async_session_factory, init_db
from app.models import Advisory, AlertCluster, Farmer, Observation, SatelliteNDVI, WikiArticle
from app.services.demo_seed import seed_demo_memory_palace
from scripts.seed_data import load_wiki_articles


async def main() -> int:
    await init_db()
    await load_wiki_articles()
    async with async_session_factory() as db:
        summary = await seed_demo_memory_palace(db, telegram_user_id="demo_farmer")
        counts = {
            "farmers": await db.scalar(select(func.count(Farmer.id))),
            "wiki_articles": await db.scalar(select(func.count(WikiArticle.id))),
            "observations": await db.scalar(select(func.count(Observation.id))),
            "advisories": await db.scalar(select(func.count(Advisory.id))),
            "ndvi_points": await db.scalar(select(func.count(SatelliteNDVI.id))),
            "clusters": await db.scalar(select(func.count(AlertCluster.id))),
        }

    ollama_status = await _check_ollama()

    print("AgriMesh demo readiness")
    print("=======================")
    print(f"Demo farmer id: {summary['farmer_id']}")
    print(f"Demo crop: {summary['crop_name']} ({summary['crop_stage']})")
    for key, value in counts.items():
        print(f"{key}: {value}")
    print(f"Ollama: {ollama_status}")
    print("")
    print("Telegram demo flow:")
    print("1. Set TELEGRAM_BOT_TOKEN in .env")
    print("2. Run: python -m app.bot.telegram_bot")
    print("3. In Telegram send: /demo")
    print("4. Try: /memory, /prices, /finance, and 'pattiyon pe brown spots hain'")
    return 0


async def _check_ollama() -> str:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{settings.ollama_host}/api/tags")
            response.raise_for_status()
            models = [model.get("name", "") for model in response.json().get("models", [])]
    except Exception as exc:
        return f"not reachable at {settings.ollama_host} ({exc})"

    if any(settings.ollama_model in model for model in models):
        return f"ready: {settings.ollama_model}"
    if any(settings.ollama_fallback_model in model for model in models):
        return f"fallback ready: {settings.ollama_fallback_model}; primary missing"
    return f"running, but configured model missing. Available: {', '.join(models[:5]) or 'none'}"


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
