"""
AgriMesh V4.0 — Mandi (Market Price) Service
Seeded APMC prices with real API shape (ready for agmarknet.gov.in / data.gov.in swap).
"""
from __future__ import annotations

import json
from datetime import timedelta

from app.config import settings
from app.utils.time import utc_now

# ─── Seed Data ────────────────────────────────────────────────────────

def _load_seed_mandi() -> dict:
    seed_path = settings.seed_dir / "mandi_prices.json"
    if seed_path.exists():
        return json.loads(seed_path.read_text(encoding="utf-8"))
    return _builtin_seed()


def _builtin_seed() -> dict:
    return {
        "rice": {
            "paddy_basmati": {"min": 2200, "max": 2800, "modal": 2500, "unit": "₹/quintal"},
            "paddy_common": {"min": 1950, "max": 2300, "modal": 2150, "unit": "₹/quintal"},
            "msp_2025_26": 2300,
        },
        "wheat": {
            "grain": {"min": 2350, "max": 2650, "modal": 2500, "unit": "₹/quintal"},
            "msp_2025_26": 2425,
        },
        "maize": {
            "grain": {"min": 2000, "max": 2400, "modal": 2200, "unit": "₹/quintal"},
            "msp_2025_26": 2225,
        },
        "pulses": {
            "moong": {"min": 8000, "max": 9500, "modal": 8700, "unit": "₹/quintal"},
            "arhar": {"min": 7500, "max": 9000, "modal": 8300, "unit": "₹/quintal"},
            "msp_2025_26_moong": 8682,
            "msp_2025_26_arhar": 7550,
        },
        "last_updated": utc_now().date().isoformat(),
        "source": "seeded — agmarknet.gov.in compatible format",
        "mandis_tracked": ["Munger", "Bhagalpur", "Patna", "Begusarai", "Khagaria"],
    }


_seed = _load_seed_mandi()


async def get_mandi_prices(crop: str, district: str = "Munger", days: int = 7) -> dict:
    """
    Get recent mandi prices for a crop in a district.
    Seeded data with real agmarknet.gov.in API shape.
    """
    crop_key = crop.lower().strip()
    crop_data = _seed.get(crop_key)

    if not crop_data:
        # Try partial match
        for key in _seed:
            if key in crop_key or crop_key in key:
                crop_data = _seed[key]
                crop_key = key
                break

    if not crop_data:
        return {
            "crop": crop,
            "district": district,
            "prices_available": False,
            "message": f"No price data for {crop}. Data covers: {list(_seed.keys())}",
            "source": "seeded",
        }

    # Build price history (seeded with slight random variation for realism)
    import random
    random.seed(hash(crop_key + district) % 2**31)

    today = utc_now().date()
    price_entries = []
    for grain_type, prices in crop_data.items():
        if grain_type.startswith("msp_"):
            continue
        if isinstance(prices, dict) and "modal" in prices:
            base = prices["modal"]
            entries = []
            for d in range(days):
                variation = random.uniform(-0.05, 0.05)
                entries.append({
                    "date": (today - timedelta(days=d)).isoformat(),
                    "min": int(prices["min"] * (1 + variation)),
                    "max": int(prices["max"] * (1 + variation)),
                    "modal": int(base * (1 + variation)),
                })
            price_entries.append({"type": grain_type, "unit": prices["unit"], "history": entries})

    msp_key = "msp_2025_26"
    msp = crop_data.get(msp_key) or next(
        (v for k, v in crop_data.items() if k.startswith("msp_")), None
    )

    return {
        "crop": crop,
        "district": district,
        "prices": price_entries,
        "msp": msp,
        "last_updated": _seed.get("last_updated", utc_now().date().isoformat()),
        "source": "seeded — agmarknet.gov.in format",
        "generated_at": utc_now().isoformat(),
    }


async def get_msp(crop: str, year: str = "2025-26") -> dict:
    """Get Minimum Support Price for a crop."""
    crop_data = _seed.get(crop.lower().strip(), {})
    msp = None
    for k, v in crop_data.items():
        if k.startswith("msp_"):
            msp = v
            break

    if msp is None:
        # Try partial match
        for key, data in _seed.items():
            if crop.lower() in key and isinstance(data, dict):
                for k, v in data.items():
                    if k.startswith("msp_"):
                        msp = v
                        break

    return {
        "crop": crop,
        "year": year,
        "msp_per_quintal": msp,
        "unit": "₹/quintal",
        "source": "seeded — CACP MSP schedule",
        "note": "MSP is the government-guaranteed minimum price for procurement",
    }
