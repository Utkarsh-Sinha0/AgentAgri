"""
AgriMesh V4.0 — MCP Weather Server
Model Context Protocol server for weather data.
Exposes: get_forecast, get_historical_weather
"""
from fastmcp import FastMCP

from app.services.weather import get_forecast, get_historical_weather

mcp = FastMCP("weather-server")


@mcp.tool()
async def tool_get_forecast(field_id: str = "default", days: int = 5) -> dict:
    """Get 5-day weather forecast for a field. Returns temp, humidity, rainfall, wind."""
    return await get_forecast(field_id=field_id, days=days)


@mcp.tool()
async def tool_get_historical_weather(field_id: str = "default", days: int = 7) -> dict:
    """Get historical weather for a field (last N days)."""
    return await get_historical_weather(field_id=field_id, days=days)


if __name__ == "__main__":
    mcp.run(transport="stdio")
