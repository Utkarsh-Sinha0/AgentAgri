"""
AgriMesh V4.0 — MCP Mandi Server
Model Context Protocol server for market prices.
Exposes: get_mandi_prices, get_msp
"""
from fastmcp import FastMCP

from app.services.mandi import get_mandi_prices, get_msp

mcp = FastMCP("mandi-server")


@mcp.tool()
async def tool_get_mandi_prices(crop: str, district: str = "Munger", days: int = 7) -> dict:
    """Get recent mandi prices for a crop in a district."""
    return await get_mandi_prices(crop=crop, district=district, days=days)


@mcp.tool()
async def tool_get_msp(crop: str, year: str = "2025-26") -> dict:
    """Get Minimum Support Price (MSP) for a crop."""
    return await get_msp(crop=crop, year=year)


if __name__ == "__main__":
    mcp.run(transport="stdio")
