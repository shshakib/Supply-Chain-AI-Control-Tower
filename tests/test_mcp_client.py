from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from sqlalchemy.orm import Session

from control_tower.access import AccessService
from control_tower.agents.llm import MCPTraceHooks
from control_tower.agents.runtime import AgentRuntime
from control_tower.integrations.risk_mcp_client import (
    ALL_RISK_MCP_TOOLS,
    RiskMCPConnector,
    _risk_tool_filter,
)
from control_tower.observability import ExecutionTrace
from control_tower.synthetic import DEMO_AS_OF


class FakeMCPServer:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.cleaned_up = False

    async def connect(self) -> None:
        if self.fail:
            raise OSError("offline")

    async def list_tools(self):
        return [SimpleNamespace(name=name) for name in ALL_RISK_MCP_TOOLS]

    async def cleanup(self) -> None:
        self.cleaned_up = True


def test_connector_reports_connected_tool_inventory(monkeypatch) -> None:
    server = FakeMCPServer()
    connector = RiskMCPConnector(
        enabled=True,
        url="http://risk.test/mcp",
        connect_attempts=1,
    )
    monkeypatch.setattr(connector, "_create_server", lambda: server)

    status = asyncio.run(connector.connect())

    assert status.state == "connected"
    assert set(status.tools) == ALL_RISK_MCP_TOOLS

    asyncio.run(connector.close())
    assert server.cleaned_up is True


def test_connector_degrades_to_local_analysis_when_server_is_offline(monkeypatch) -> None:
    server = FakeMCPServer(fail=True)
    connector = RiskMCPConnector(
        enabled=True,
        url="http://risk.test/mcp",
        connect_attempts=1,
    )
    monkeypatch.setattr(connector, "_create_server", lambda: server)

    status = asyncio.run(connector.connect())

    assert status.state == "unavailable"
    assert connector.server is None
    assert "local analysis remains available" in (status.detail or "")
    assert server.cleaned_up is True


def test_connector_degrades_when_mcp_dependency_cannot_initialize(monkeypatch) -> None:
    connector = RiskMCPConnector(
        enabled=True,
        url="http://risk.test/mcp",
        connect_attempts=1,
    )

    def fail_to_create():
        raise ImportError("mcp package unavailable")

    monkeypatch.setattr(connector, "_create_server", fail_to_create)

    status = asyncio.run(connector.connect())

    assert status.state == "unavailable"
    assert "ImportError" in (status.detail or "")


def test_each_specialist_receives_only_its_mcp_tools() -> None:
    shipment_context = SimpleNamespace(agent=SimpleNamespace(name="Shipment specialist"))
    supplier_context = SimpleNamespace(agent=SimpleNamespace(name="Supplier risk specialist"))
    compliance_tool = SimpleNamespace(name="get_trade_compliance_advisories")
    carrier_tool = SimpleNamespace(name="get_carrier_advisories")

    assert _risk_tool_filter(shipment_context, carrier_tool) is True
    assert _risk_tool_filter(shipment_context, compliance_tool) is False
    assert _risk_tool_filter(supplier_context, carrier_tool) is False


def test_mcp_hook_records_source_aware_evidence(session: Session) -> None:
    access = AccessService(session).resolve(
        "noah.east@controltower.demo",
        "meridian-assembly",
    )
    runtime = AgentRuntime(
        session=session,
        access=access,
        as_of=DEMO_AS_OF,
        retriever=SimpleNamespace(),
        trace=ExecutionTrace(run_id="mcp-hook-test"),
    )
    context = SimpleNamespace(
        context=runtime,
        tool_arguments=json.dumps({"location_query": "Vancouver"}),
        tool_call_id="call-test",
    )
    hook = MCPTraceHooks()
    agent = SimpleNamespace(name="Shipment specialist")
    tool = SimpleNamespace(name="external_risk_feed_search_disruption_events")

    asyncio.run(
        hook.on_tool_start(
            context,
            agent,
            tool,
        )
    )
    asyncio.run(
        hook.on_tool_end(
            context,
            agent,
            tool,
            json.dumps({"count": 1, "events": [{"event_id": "EXT-2026-001"}]}),
        )
    )

    assert runtime.events[0].tool == "mcp:search_disruption_events"
    assert runtime.events[0].source == "mcp"
    assert runtime.events[0].result_count == 1
    assert [event.status for event in runtime.trace.events] == ["started", "completed"]
    assert runtime.trace.events[0].node == "mcp"
    assert runtime.trace.events[1].details == {
        "tool": "search_disruption_events",
        "result_count": 1,
    }
