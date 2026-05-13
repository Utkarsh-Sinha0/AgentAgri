"""
AgriMesh V4.0 — MCP Finance Server
Model Context Protocol server for farm finance tracking.
"""
from fastmcp import FastMCP

from app.services.finance import compute_pnl, log_expense, log_revenue

mcp = FastMCP("finance-server")


@mcp.tool()
async def tool_log_expense(
    farmer_id: str,
    crop_cycle_id: str,
    category: str,
    amount: float,
    description: str = "",
) -> dict:
    """Log a farming expense (seed, fertilizer, pesticide, labour, irrigation, etc.)."""
    # We need a db session — in MCP context, we create a new one
    from app.database import async_session_factory
    async with async_session_factory() as db:
        return await log_expense(
            db=db,
            farmer_id=farmer_id,
            crop_cycle_id=crop_cycle_id,
            category=category,
            amount=amount,
            description=description,
        )


@mcp.tool()
async def tool_log_revenue(
    farmer_id: str,
    crop_cycle_id: str,
    category: str,
    amount: float,
    description: str = "",
) -> dict:
    """Log farming revenue (harvest sale)."""
    from app.database import async_session_factory
    async with async_session_factory() as db:
        return await log_revenue(
            db=db,
            farmer_id=farmer_id,
            crop_cycle_id=crop_cycle_id,
            category=category,
            amount=amount,
            description=description,
        )


@mcp.tool()
async def tool_compute_pnl(farmer_id: str, crop_cycle_id: str) -> dict:
    """Compute profit/loss for a crop cycle."""
    from app.database import async_session_factory
    async with async_session_factory() as db:
        return await compute_pnl(db=db, farmer_id=farmer_id, crop_cycle_id=crop_cycle_id)


if __name__ == "__main__":
    mcp.run(transport="stdio")
