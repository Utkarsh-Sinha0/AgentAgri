"""
AgriMesh V4.0 — MCP Scheme Server
Model Context Protocol server for government scheme matching.
"""
from fastmcp import FastMCP

from app.services.scheme import match_schemes

mcp = FastMCP("scheme-server")


@mcp.tool()
async def tool_match_schemes(
    farmer_profile: dict | None = None,
    field: dict | None = None,
    crop: str | None = None,
) -> dict:
    """Match farmer to eligible government schemes (PM-KISAN, PMFBY, KCC, SHC, PKVY)."""
    return await match_schemes(farmer_profile=farmer_profile, field=field, crop=crop)


if __name__ == "__main__":
    mcp.run(transport="stdio")
