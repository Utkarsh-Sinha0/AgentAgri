"""
AgriMesh V4.0 — Weather Service
Live OpenWeatherMap integration with seeded fallback for demo.
"""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import timedelta

import httpx
from loguru import logger

from app.config import settings
from app.utils.time import utc_now

_OWM_FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"
_OWM_CURRENT_URL = "https://api.openweathermap.org/data/2.5/weather"
_OWM_TIMEOUT = 10.0

# Default coordinates (Munger, Bihar) used when no field location is available
_DEFAULT_LAT = 25.38
_DEFAULT_LNG = 86.47
_DEFAULT_DISTRICT = "Munger"


# ─── OpenWeatherMap Live ─────────────────────────────────────────────

def _owm_available() -> bool:
    return bool(settings.weather_api_key)


def _owm_condition(weather_main: str) -> str:
    mapping = {
        "Clear": "sunny",
        "Clouds": "partly_cloudy",
        "Rain": "rain",
        "Drizzle": "rain",
        "Thunderstorm": "storm",
        "Snow": "snow",
        "Mist": "foggy",
        "Haze": "foggy",
        "Fog": "foggy",
    }
    return mapping.get(weather_main, "partly_cloudy")


async def _owm_forecast(lat: float, lng: float, days: int) -> list[dict]:
    """Fetch 5-day/3-hour forecast from OpenWeatherMap, aggregate to daily."""
    async with httpx.AsyncClient(timeout=_OWM_TIMEOUT) as client:
        resp = await client.get(
            _OWM_FORECAST_URL,
            params={
                "lat": lat,
                "lon": lng,
                "appid": settings.weather_api_key,
                "units": "metric",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    daily: dict[str, dict] = {}
    for entry in data.get("list", []):
        date = entry["dt_txt"][:10]
        if date not in daily:
            daily[date] = {
                "date": date,
                "temp_max": -999,
                "temp_min": 999,
                "humidity": 0,
                "rainfall_mm": 0.0,
                "wind_kmh": 0,
                "condition": "sunny",
                "_humidity_count": 0,
                "_wind_count": 0,
            }
        d = daily[date]
        main = entry.get("main", {})
        d["temp_max"] = max(d["temp_max"], main.get("temp_max", main.get("temp", 0)))
        d["temp_min"] = min(d["temp_min"], main.get("temp_min", main.get("temp", 50)))
        d["humidity"] += main.get("humidity", 0)
        d["_humidity_count"] += 1
        rain_3h = entry.get("rain", {}).get("3h", 0)
        d["rainfall_mm"] = round(d["rainfall_mm"] + rain_3h, 1)
        wind_ms = entry.get("wind", {}).get("speed", 0)
        d["wind_kmh"] += round(wind_ms * 3.6)
        d["_wind_count"] += 1
        weather_main = (entry.get("weather") or [{}])[0].get("main", "")
        if weather_main in ("Rain", "Drizzle", "Thunderstorm", "Snow"):
            d["condition"] = _owm_condition(weather_main)

    result = []
    for date in sorted(daily.keys())[:days]:
        d = daily[date]
        h_count = d.pop("_humidity_count", 1) or 1
        w_count = d.pop("_wind_count", 1) or 1
        d["humidity"] = round(d["humidity"] / h_count)
        d["wind_kmh"] = round(d["wind_kmh"] / w_count)
        if d["condition"] == "sunny" and d["humidity"] > 75:
            d["condition"] = "partly_cloudy"
        result.append(d)

    return result


async def _owm_current(lat: float, lng: float) -> dict:
    """Fetch current weather for historical-ish context."""
    async with httpx.AsyncClient(timeout=_OWM_TIMEOUT) as client:
        resp = await client.get(
            _OWM_CURRENT_URL,
            params={
                "lat": lat,
                "lon": lng,
                "appid": settings.weather_api_key,
                "units": "metric",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    main = data.get("main", {})
    rain_1h = data.get("rain", {}).get("1h", 0)
    weather_main = (data.get("weather") or [{}])[0].get("main", "Clear")
    return {
        "date": utc_now().date().isoformat(),
        "temp_max": main.get("temp_max", main.get("temp", 0)),
        "temp_min": main.get("temp_min", main.get("temp", 0)),
        "humidity": main.get("humidity", 0),
        "rainfall_mm": round(rain_1h, 1),
        "wind_kmh": round(data.get("wind", {}).get("speed", 0) * 3.6),
        "condition": _owm_condition(weather_main),
    }


# ─── Seed Data (fallback) ───────────────────────────────────────────

def _load_seed_weather() -> dict:
    seed_path = settings.seed_dir / "weather.json"
    if seed_path.exists():
        return _normalize_seed_dates(json.loads(seed_path.read_text(encoding="utf-8")))
    return _builtin_seed()


def _normalize_seed_dates(seed: dict) -> dict:
    normalized = deepcopy(seed)
    today = utc_now().date()
    for field_data in normalized.values():
        forecast = field_data.get("forecast") or []
        for index, row in enumerate(forecast):
            row["date"] = (today + timedelta(days=index)).isoformat()
        historical = field_data.get("historical", {}).get("last_7_days") or []
        start_offset = -len(historical)
        for index, row in enumerate(historical):
            row["date"] = (today + timedelta(days=start_offset + index)).isoformat()
    return normalized


def _builtin_seed() -> dict:
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
            "lat": _DEFAULT_LAT,
            "lng": _DEFAULT_LNG,
            "district": _DEFAULT_DISTRICT,
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


def _field_coords(field_id: str | None) -> tuple[float, float, str]:
    """Get lat/lng/district for a field, falling back to seed defaults."""
    field_data = _seed.get(field_id) if field_id else _seed.get("default_field")
    if not field_data:
        field_data = _seed["default_field"]
    return (
        field_data.get("lat", _DEFAULT_LAT),
        field_data.get("lng", _DEFAULT_LNG),
        field_data.get("district", _DEFAULT_DISTRICT),
    )


# ─── Public API ──────────────────────────────────────────────────────

async def get_forecast(field_id: str | None = None, days: int = 5) -> dict:
    """Get weather forecast — live from OpenWeatherMap if key is set, else seeded."""
    lat, lng, district = _field_coords(field_id)

    if _owm_available():
        try:
            forecast = await _owm_forecast(lat, lng, days)
            logger.info("Weather forecast from OpenWeatherMap: {} days for {}", len(forecast), district)
            return {
                "field_id": field_id or "default",
                "district": district,
                "forecast": forecast,
                "source": "openweathermap",
                "generated_at": utc_now().isoformat(),
            }
        except Exception as exc:
            logger.warning("OpenWeatherMap forecast failed, falling back to seed: {}", exc)

    # Fallback to seed
    field_data = _seed.get(field_id) if field_id else _seed.get("default_field")
    if not field_data:
        field_data = _seed["default_field"]

    return {
        "field_id": field_id or "default",
        "district": field_data.get("district", "Unknown"),
        "forecast": field_data["forecast"][:days],
        "source": "seeded",
        "generated_at": utc_now().isoformat(),
    }


async def get_historical_weather(field_id: str | None = None, days: int = 7) -> dict:
    """Get historical weather — live current snapshot if key is set, else seeded."""
    lat, lng, district = _field_coords(field_id)

    if _owm_available():
        try:
            current = await _owm_current(lat, lng)
            current["date"] = (utc_now().date() - timedelta(days=1)).isoformat()
            logger.info("Current weather from OpenWeatherMap for {}", district)
            return {
                "field_id": field_id or "default",
                "historical_days": [current],
                "total_rainfall_30d_mm": current["rainfall_mm"],
                "avg_temp_30d": round((current["temp_max"] + current["temp_min"]) / 2, 1),
                "source": "openweathermap",
                "generated_at": utc_now().isoformat(),
            }
        except Exception as exc:
            logger.warning("OpenWeatherMap current failed, falling back to seed: {}", exc)

    # Fallback to seed
    field_data = _seed.get(field_id) if field_id else _seed.get("default_field")
    if not field_data:
        field_data = _seed["default_field"]

    historical = field_data.get("historical", {})
    return {
        "field_id": field_id or "default",
        "historical_days": historical.get("last_7_days", [])[:days],
        "total_rainfall_30d_mm": historical.get("total_rainfall_30d_mm", 0),
        "avg_temp_30d": historical.get("avg_temp_30d", 0),
        "source": "seeded",
        "generated_at": utc_now().isoformat(),
    }
