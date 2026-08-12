from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from typing import Any

logger = logging.getLogger(__name__)

SHIPMENT_MCP_TOOLS = frozenset(
    {
        "search_disruption_events",
        "get_lane_status",
        "get_carrier_advisories",
    }
)
SUPPLIER_MCP_TOOLS = frozenset(
    {
        "search_disruption_events",
        "get_supplier_external_signals",
    }
)
CONTRACT_MCP_TOOLS = frozenset({"get_trade_compliance_advisories"})
ALL_RISK_MCP_TOOLS = SHIPMENT_MCP_TOOLS | SUPPLIER_MCP_TOOLS | CONTRACT_MCP_TOOLS

AGENT_TOOL_ALLOWLIST = {
    "Shipment specialist": SHIPMENT_MCP_TOOLS,
    "Supplier risk specialist": SUPPLIER_MCP_TOOLS,
    "Contracts and compliance specialist": CONTRACT_MCP_TOOLS,
}


@dataclass(frozen=True)
class RiskMCPStatus:
    enabled: bool
    state: str
    url: str
    tools: tuple[str, ...] = ()
    detail: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class RiskMCPConnector:
    """Own the Agents SDK connection to the external risk-feed MCP server."""

    def __init__(
        self,
        *,
        enabled: bool,
        url: str,
        connect_timeout_seconds: float = 2.0,
        connect_attempts: int = 3,
    ) -> None:
        self.enabled = enabled
        self.url = url
        self.connect_timeout_seconds = connect_timeout_seconds
        self.connect_attempts = connect_attempts
        self.server: Any | None = None
        self.status = RiskMCPStatus(
            enabled=enabled,
            state="not_started" if enabled else "disabled",
            url=url,
        )

    async def connect(self) -> RiskMCPStatus:
        if not self.enabled:
            self.status = RiskMCPStatus(enabled=False, state="disabled", url=self.url)
            return self.status
        if self.server is not None:
            return self.status

        last_error: Exception | None = None
        for attempt in range(1, self.connect_attempts + 1):
            candidate: Any | None = None
            try:
                candidate = self._create_server()
                await asyncio.wait_for(
                    candidate.connect(),
                    timeout=self.connect_timeout_seconds + 1,
                )
                tools = await _advertised_tools(candidate)
                tool_names = tuple(
                    sorted(tool.name for tool in tools if tool.name in ALL_RISK_MCP_TOOLS)
                )
                missing = ALL_RISK_MCP_TOOLS.difference(tool_names)
                if missing:
                    raise RuntimeError(
                        "Risk MCP server is missing required tools: " + ", ".join(sorted(missing))
                    )
            except Exception as exc:
                last_error = exc
                if candidate is not None:
                    await self._cleanup_candidate(candidate)
                if attempt < self.connect_attempts:
                    await asyncio.sleep(0.35 * attempt)
                continue

            self.server = candidate
            self.status = RiskMCPStatus(
                enabled=True,
                state="connected",
                url=self.url,
                tools=tool_names,
                detail="Synthetic external disruption feed is available.",
            )
            return self.status

        error_name = type(last_error).__name__ if last_error is not None else "ConnectionError"
        self.status = RiskMCPStatus(
            enabled=True,
            state="unavailable",
            url=self.url,
            detail=f"External feed unavailable ({error_name}); local analysis remains available.",
        )
        logger.warning("Risk MCP service unavailable at %s (%s)", self.url, error_name)
        return self.status

    async def close(self) -> None:
        if self.server is None:
            return
        server, self.server = self.server, None
        await self._cleanup_candidate(server)
        self.status = RiskMCPStatus(
            enabled=self.enabled,
            state="closed",
            url=self.url,
        )

    def _create_server(self) -> Any:
        # Lazy import keeps the core offline demo usable even if optional MCP startup fails.
        from agents.mcp import MCPServerStreamableHttp

        return MCPServerStreamableHttp(
            name="external_risk_feed",
            params={
                "url": self.url,
                "timeout": self.connect_timeout_seconds,
                "sse_read_timeout": 30,
            },
            cache_tools_list=True,
            client_session_timeout_seconds=10,
            tool_filter=_risk_tool_filter,
            use_structured_content=True,
            max_retry_attempts=1,
            retry_backoff_seconds_base=0.25,
            require_approval="never",
        )

    @staticmethod
    async def _cleanup_candidate(server: Any) -> None:
        try:
            await server.cleanup()
        except Exception as exc:  # pragma: no cover - defensive SDK cleanup path
            logger.debug("Ignoring MCP cleanup error: %s", type(exc).__name__)


def _risk_tool_filter(context: Any, tool: Any) -> bool:
    agent = getattr(context, "agent", None)
    agent_name = getattr(agent, "name", None)
    if agent_name is None:
        return tool.name in ALL_RISK_MCP_TOOLS
    return tool.name in AGENT_TOOL_ALLOWLIST.get(agent_name, frozenset())


async def _advertised_tools(server: Any) -> list[Any]:
    """Read the raw server inventory before an agent-specific filter is applicable."""

    session = getattr(server, "session", None)
    if session is not None:
        result = await session.list_tools()
        return list(result.tools)
    return list(await server.list_tools())
