"""
AgriMesh V4.0 — Weather Service
Seeded forecast data with real API shape (ready for OpenWeatherMap / IMD swap).
"""
from __future__ import annotations

import json
from datetime import timedelta

from app.config import settings
from app.utils.time import utc_now

# ─── Seed Data ────────────────────────────────────────────────────────

def _load_seed_weather() -> dict:
    seed_path = settings.seed_dir / "weather.json"
    if seed_path.exists():
        return json.loads(seed_path.read_text(encoding="utf-8"))
    # Built-in seed for demo
    return _builtin_seed()


def _builtin_seed() -> dict:
    """Built-in seed data for Bihar region (demo). Dates anchored to utcnow() so
    the demo never goes stale (H18)."""
    today = utc_now().date()
    forecast_rows = [
        {"offset": 0, "temp_max": 38, "temp_min": 26, "humidity": 65, "rainfall_mm": 0, "wind_kmh": 12, "condition": "sunny"},
        {"offset": 1, "temp_max": 37, "temp_min": 25, "humidity": 70, "rainfall_mm": 2.5, "wind_kmh": 15, "condition": "partly_cloudy"},
        {"offset": 2, "temp_max": 35, "temp_min": 24, "humidity": 80, "rainfall_mm": 15.0, "wind_kmh": 20, "condition": "rain"},
        {"offset": 3, "temp_max": 33, "temp_min": 24, "humidity": 85, "rainfall_mm": 8.0, "wind_kmh": 18, "condition": "rain"},
        {"offset": 4, "temp_max": 36, "temp_min": 25, "humidity": 72, "rainfall_mm": 0.5, "wind_kmh": 10, "condition": "partly_cloudy"},
    ]
    historical_rows = [
        {"offset": -7, "temp_max": 39, "temp_min": 27, "rainfall_mm": 0},
        {"offset": -6, "temp_max": 40, "temp_min": 28, "rainfall_mm": 0},
        {"offset": -5, "temp_max": 41, "temp_min": 28, "rainfall_mm": 0},
        {"offset": -4, "temp_max": 39, "temp_min": 27, "rainfall_mm": 0},
        {"offset": -3, "temp_max": 40, "temp_min": 27, "rainfall_mm": 0},
        {"offset": -2, "temp_max": 38, "temp_min": 26, "rainfall_mm": 1.2},
        {"offset": -1, "temp_max": 37, "temp_min": 25, "rainfall_mm": 3.8},
    ]
    return {
        "default_field": {
            "lat": 25.38,
            "lng": 86.47,
            "district": "Munger",
            "state": "Bihar",
            "forecast": [
                {**{k: v for k, v in row.items() if k != "offset"},
                 "date": (today + timedelta(days=row["offset"])).isoformat()}
                for row in forecast_rows
            ],
            "historical": {
                "last_7_days": [
                    {**{k: v for k, v in row.items() if k != "offset"},
                     "date": (today + timedelta(days=row["offset"])).isoformat()}
                    for row in historical_rows
                ],
                "total_rainfall_30d_mm": 45.2,
                "avg_temp_30d": 36.8,
            },
        }
    }


_seed = _load_seed_weather()


async def get_forecast(field_id: str | None = None, days: int = 5) -> dict:
    """
    Get weather forecast for a field.
    Seeded data with real API shape. Swap to OpenWeatherMap/IMD for production.
    """
    field_data = _seed.get(field_id) if field_id else _seed.get("default_field")
    if not field_data:
        field_data = _seed["default_field"]

    forecast = field_data["forecast"][:days]
    return {
        "field_id": field_id or "default",
        "district": field_data.get("district", "Unknown"),
        "forecast": forecast,
        "source": "seeded",
        "generated_at": utc_now().isoformat(),
    }


async def get_historical_weather(field_id: str | None = None, days: int = 7) -> dict:
    """Get historical weather data for a field."""
    field_data = _seed.get(field_id) if field_id else _seed.get("default_field")
    if not field_data:
        field_data = _seed["default_field"]

    historical = field_data.get("historical", {})
    recent = historical.get("last_7_days", [])[:days]

    return {
        "field_id": field_id or "default",
        "historical_days": recent,
        "total_rainfall_30d_mm": historical.get("total_rainfall_30d_mm", 0),
        "avg_temp_30d": historical.get("avg_temp_30d", 0),
        "source": "seeded",
        "generated_at": utc_now().isoformat(),
    }
