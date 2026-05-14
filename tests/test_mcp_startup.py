from __future__ import annotations

import sys

import pytest
from fastmcp import Client
from fastmcp.client.transports.stdio import StdioTransport


@pytest.mark.asyncio
async def test_weather_mcp_server_starts_and_serves_forecast():
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "app.mcp_servers.weather_server"],
        cwd=".",
    )

    async with Client(transport, timeout=5, init_timeout=5) as client:
        result = await client.call_tool(
            "tool_get_forecast",
            {"field_id": "default", "days": 1},
        )

    assert result.data["forecast"]
