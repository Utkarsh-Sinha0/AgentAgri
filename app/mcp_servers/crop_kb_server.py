"""
AgentAgri V4.0 - MCP Crop KB Server (:9005)

Exposes universal-KB lookups: stage guidance, MSP, insurance, cold storage,
sustainable alternatives. Backed by app/services/universal_kb.py.
"""
from fastmcp import FastMCP

from app.services import universal_kb

mcp = FastMCP("crop-kb-server")


@mcp.tool()
async def tool_get_stage_guidance(crop: str, stage: str | None = None, state: str | None = None) -> dict:
    """Return playbook stage slice for a crop (rice|wheat); whole playbook if stage omitted."""
    result = universal_kb.get_stage_guidance(crop=crop, stage=stage)
    return {"crop": crop, "stage": stage, "state": state, "data": result}


@mcp.tool()
async def tool_get_msp(crop: str, state: str | None = None, season: str | None = None, variety: str | None = None) -> dict:
    """Return seeded MSP rows for a crop, optionally filtered by state/season/variety."""
    rows = universal_kb.get_msp(crop=crop, state=state, season=season, variety=variety)
    return {"crop": crop, "state": state, "season": season, "variety": variety, "rows": rows}


@mcp.tool()
async def tool_get_insurance_options(crop: str, state: str | None = None, season: str | None = None) -> dict:
    """Return insurance policies applicable for a crop and state."""
    rows = universal_kb.get_insurance(crop=crop, state=state, season=season)
    return {"crop": crop, "state": state, "season": season, "policies": rows}


@mcp.tool()
async def tool_get_cold_storage_nearby(district: str | None = None, state: str | None = None, crop: str | None = None) -> dict:
    """Return cold storage facilities filtered by district/state/crop."""
    rows = universal_kb.get_cold_storage(district=district, state=state, crop=crop)
    return {"district": district, "state": state, "crop": crop, "facilities": rows}


@mcp.tool()
async def tool_get_sustainable_alternatives(crop: str, stage: str | None = None) -> dict:
    """Return sustainable alternatives for a crop (optionally a specific stage)."""
    alternatives = universal_kb.get_sustainable_alternatives(crop=crop, stage=stage)
    return {"crop": crop, "stage": stage, "alternatives": alternatives}


@mcp.tool()
async def tool_get_state_schemes(state: str | None = None, crop: str | None = None) -> dict:
    """Return state-specific schemes filtered by state and applicable crop."""
    rows = universal_kb.get_state_schemes(state=state, crop=crop)
    return {"state": state, "crop": crop, "schemes": rows}


if __name__ == "__main__":
    universal_kb.load_seed()
    mcp.run(transport="stdio")
