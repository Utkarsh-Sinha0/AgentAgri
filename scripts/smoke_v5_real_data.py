"""V5 no-mock workflow smoke.

Uses the configured database plus real local seed files / configured live APIs.
It does not mock Telegram, Sarvam, Ollama, weather, mandi, or memory services.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.database import async_session_factory, init_db
from app.models import CropCycle
from app.services.crop_cycle import advance_stage
from app.services.demo_seed import seed_demo_memory_palace
from app.services.farmer_dashboard import get_farmer_dashboard
from app.services.market_intel import sell_decision_advisor
from app.services.memory import run_coarsening_job
from app.services.rotation import suggest_next_crop
from app.services.weather import get_forecast, get_historical_weather


async def main() -> int:
    await init_db()
    async with async_session_factory() as db:
        seed = await seed_demo_memory_palace(db, telegram_user_id="v5-smoke-farmer")
        dashboard = await get_farmer_dashboard(db, farmer_id=seed["farmer_id"])
        forecast = await get_forecast(field_id=seed["field_id"], days=2)
        history = await get_historical_weather(field_id=seed["field_id"], days=1)
        sell = await sell_decision_advisor(seed["crop_name"], district="Munger")

        cycle = await db.scalar(select(CropCycle).where(CropCycle.id == seed["crop_cycle_id"]))
        stage = await advance_stage(db, cycle) if cycle else None
        rotation = await suggest_next_crop(db, seed["field_id"])
        coarsening = await run_coarsening_job(db)

        checks = {
            "dashboard_fields": bool(dashboard.get("fields")),
            "dashboard_threads": "threads" in dashboard["fields"][0],
            "weather_forecast": bool(forecast.get("forecast")),
            "weather_history": bool(history.get("historical_days")),
            "sell_decision": sell.get("decision") in {"SELL_NOW", "WAIT", "PARTIAL_SALE", "HOLD", "INSUFFICIENT_DATA"},
            "storage_advice": bool(sell.get("storage_advice")),
            "stage_checked": cycle is not None and (stage is None or cycle.current_stage == stage),
            "rotation": bool(rotation.get("next_crop")),
            "coarsening": any(v >= 0 for v in coarsening.values()),
        }

    failed = [name for name, ok in checks.items() if not ok]
    print("V5 no-mock smoke")
    print("================")
    for name, ok in checks.items():
        print(f"{name}: {'OK' if ok else 'FAIL'}")
    if failed:
        print(f"FAILED: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
