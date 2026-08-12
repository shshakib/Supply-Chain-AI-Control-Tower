from __future__ import annotations

import argparse

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Settings as FastMCPSettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from control_tower.integrations.risk_feed import (
    RISK_FEED_AS_OF,
    EventSearchResult,
    EventType,
    LaneStatusResult,
    RiskFeedRepository,
    RiskSeverity,
    SupplierSignalResult,
    build_synthetic_risk_feed,
    parse_feed_date,
)

# MCP 1.x can leave this generic forward reference unresolved with newer Pydantic releases.
FastMCPSettings.model_rebuild()


def create_risk_mcp_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8010,
    repository: RiskFeedRepository | None = None,
) -> FastMCP:
    repo = repository or build_synthetic_risk_feed()
    server = FastMCP(
        name="GlobalRoute Synthetic Risk Feed",
        instructions=(
            "Read-only synthetic external intelligence for logistics disruptions, carrier "
            "advisories, supplier watch signals, and trade-compliance events. Return stable "
            "external-risk references with every factual event."
        ),
        host=host,
        port=port,
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
    )

    @server.tool(structured_output=True)
    def search_disruption_events(
        location_query: str | None = None,
        carrier_name: str | None = None,
        supplier_code: str | None = None,
        active_on: str = RISK_FEED_AS_OF.isoformat(),
        minimum_severity: RiskSeverity = "low",
        limit: int = 10,
    ) -> EventSearchResult:
        """Search active external disruptions by location, carrier, or supplier.

        Use identifiers learned from authorized operational tools. Results are public,
        synthetic signals and establish correlation, not a confirmed shipment root cause.
        """

        query_date = parse_feed_date(active_on)
        events = repo.search(
            active_on=query_date,
            location_query=location_query,
            carrier_name=carrier_name,
            supplier_code=supplier_code,
            minimum_severity=minimum_severity,
            limit=limit,
        )
        return EventSearchResult(active_on=query_date, count=len(events), events=events)

    @server.tool(structured_output=True)
    def get_lane_status(
        origin: str,
        destination: str,
        active_on: str = RISK_FEED_AS_OF.isoformat(),
    ) -> LaneStatusResult:
        """Assess external disruption signals touching a named origin-destination lane."""

        return repo.lane_status(origin, destination, active_on=parse_feed_date(active_on))

    @server.tool(structured_output=True)
    def get_carrier_advisories(
        carrier_name: str,
        active_on: str = RISK_FEED_AS_OF.isoformat(),
        limit: int = 10,
    ) -> EventSearchResult:
        """Return active external advisories for a carrier named by an operational record."""

        query_date = parse_feed_date(active_on)
        events = repo.search(
            active_on=query_date,
            carrier_name=carrier_name,
            event_types={"carrier_advisory", "port_disruption", "weather"},
            limit=limit,
        )
        return EventSearchResult(active_on=query_date, count=len(events), events=events)

    @server.tool(structured_output=True)
    def get_supplier_external_signals(
        supplier_code: str,
        active_on: str = RISK_FEED_AS_OF.isoformat(),
    ) -> SupplierSignalResult:
        """Return public external-risk signals for a supplier code from an authorized record."""

        return repo.supplier_signals(supplier_code, active_on=parse_feed_date(active_on))

    @server.tool(structured_output=True)
    def get_trade_compliance_advisories(
        country_code: str | None = None,
        supplier_code: str | None = None,
        active_on: str = RISK_FEED_AS_OF.isoformat(),
        limit: int = 10,
    ) -> EventSearchResult:
        """Return active synthetic trade-compliance advisories by country or supplier."""

        if not country_code and not supplier_code:
            raise ValueError("country_code or supplier_code is required")
        query_date = parse_feed_date(active_on)
        event_types: set[EventType] = {"trade_compliance"}
        events = repo.search(
            active_on=query_date,
            country_code=country_code,
            supplier_code=supplier_code,
            event_types=event_types,
            limit=limit,
        )
        return EventSearchResult(active_on=query_date, count=len(events), events=events)

    @server.resource("risk-feed://methodology")
    def methodology() -> str:
        """Explain the synthetic feed's interpretation and confidence rules."""

        return (
            "# GlobalRoute Synthetic Intelligence methodology\n\n"
            "This portfolio feed is entirely synthetic. Severity estimates operational impact; "
            "confidence estimates confidence that the event is correctly characterized. Events "
            "may correlate with an internal shipment but do not prove causation. Consumers should "
            "preserve each `external-risk:` reference and combine the signal with authorized "
            "operational records."
        )

    @server.resource("risk-feed://events/{event_id}")
    def event_resource(event_id: str) -> str:
        """Read one complete synthetic risk event as JSON."""

        return repo.get(event_id).model_dump_json(indent=2)

    @server.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "service": "globalroute-synthetic-risk-feed",
                "transport": "streamable-http",
                "mcp_endpoint": "/mcp",
                "synthetic": True,
                "event_count": len(repo.events),
                "as_of": RISK_FEED_AS_OF.isoformat(),
            }
        )

    return server


def main() -> None:
    parser = argparse.ArgumentParser(prog="control-tower-risk-mcp")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()
    create_risk_mcp_server(host=args.host, port=args.port).run(transport="streamable-http")


if __name__ == "__main__":
    main()
