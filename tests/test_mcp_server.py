from __future__ import annotations

import asyncio

from control_tower.integrations.risk_mcp_server import create_risk_mcp_server


def test_risk_mcp_server_advertises_only_read_tools() -> None:
    server = create_risk_mcp_server()

    tools = asyncio.run(server.list_tools())

    assert {tool.name for tool in tools} == {
        "search_disruption_events",
        "get_lane_status",
        "get_carrier_advisories",
        "get_supplier_external_signals",
        "get_trade_compliance_advisories",
    }
    forbidden_scope_arguments = {"user_email", "organization_id", "warehouse_ids", "tenant_id"}
    for tool in tools:
        assert forbidden_scope_arguments.isdisjoint(tool.inputSchema.get("properties", {}))


def test_risk_mcp_tool_returns_structured_cited_evidence() -> None:
    server = create_risk_mcp_server()

    _content, structured = asyncio.run(
        server.call_tool(
            "search_disruption_events",
            {
                "location_query": "Vancouver",
                "active_on": "2026-06-30",
            },
        )
    )

    assert structured["count"] == 1
    assert structured["events"][0]["reference"] == "external-risk:EXT-2026-001"
    assert structured["events"][0]["synthetic"] is True
