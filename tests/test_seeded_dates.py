from __future__ import annotations

import pytest

from app.services.mandi import get_mandi_prices
from app.services.weather import get_forecast, get_historical_weather
from app.utils.time import utc_now


@pytest.mark.asyncio
async def test_seeded_weather_dates_are_relative_to_today():
    today = utc_now().date().isoformat()

    forecast = await get_forecast(days=1)
    history = await get_historical_weather(days=1)

    assert forecast["forecast"][0]["date"] == today
    assert history["historical_days"][-1]["date"] < today


@pytest.mark.asyncio
async def test_seeded_mandi_last_updated_is_relative_to_today():
    result = await get_mandi_prices("rice", days=1)

    assert result["last_updated"] == utc_now().date().isoformat()
