from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RISK_FEED_AS_OF = date(2026, 6, 30)
RISK_FEED_SOURCE = "GlobalRoute Synthetic Intelligence"

RiskSeverity = Literal["low", "medium", "high", "critical"]
RiskLevel = Literal["clear", "low", "medium", "high", "critical"]
EventType = Literal[
    "carrier_advisory",
    "port_disruption",
    "supplier_watchlist",
    "trade_compliance",
    "weather",
]

SEVERITY_RANK: dict[str, int] = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


class DisruptionEvent(BaseModel):
    """One public, synthetic external-risk observation."""

    model_config = ConfigDict(frozen=True)

    event_id: str
    reference: str
    title: str
    event_type: EventType
    severity: RiskSeverity
    status: Literal["active", "monitoring", "resolved"]
    starts_on: date
    expected_resolution: date
    locations: list[str] = Field(default_factory=list)
    port_codes: list[str] = Field(default_factory=list)
    country_codes: list[str] = Field(default_factory=list)
    carrier_names: list[str] = Field(default_factory=list)
    supplier_codes: list[str] = Field(default_factory=list)
    expected_delay_days_min: int = 0
    expected_delay_days_max: int = 0
    summary: str
    recommended_action: str
    confidence: float = Field(ge=0, le=1)
    source: str = RISK_FEED_SOURCE
    synthetic: bool = True

    def active_on(self, value: date) -> bool:
        return self.starts_on <= value <= self.expected_resolution and self.status != "resolved"

    def searchable_text(self) -> str:
        values = [
            self.title,
            self.summary,
            *self.locations,
            *self.port_codes,
            *self.country_codes,
            *self.carrier_names,
            *self.supplier_codes,
        ]
        return " ".join(values).casefold()


class EventSearchResult(BaseModel):
    source: str = RISK_FEED_SOURCE
    synthetic: bool = True
    active_on: date
    count: int
    events: list[DisruptionEvent]


class LaneStatusResult(BaseModel):
    source: str = RISK_FEED_SOURCE
    synthetic: bool = True
    origin: str
    destination: str
    active_on: date
    risk_level: RiskLevel
    expected_delay_days_min: int
    expected_delay_days_max: int
    event_count: int
    events: list[DisruptionEvent]
    recommended_actions: list[str]


class SupplierSignalResult(BaseModel):
    source: str = RISK_FEED_SOURCE
    synthetic: bool = True
    supplier_code: str
    active_on: date
    risk_level: RiskLevel
    event_count: int
    events: list[DisruptionEvent]


class RiskFeedRepository:
    """Deterministic query layer behind the synthetic external MCP service."""

    def __init__(self, events: Sequence[DisruptionEvent]) -> None:
        self._events = tuple(events)

    @property
    def events(self) -> tuple[DisruptionEvent, ...]:
        return self._events

    def get(self, event_id: str) -> DisruptionEvent:
        normalized = event_id.strip().upper()
        for event in self._events:
            if event.event_id.upper() == normalized:
                return event
        raise ValueError(f"Unknown external-risk event: {event_id}")

    def search(
        self,
        *,
        active_on: date = RISK_FEED_AS_OF,
        location_query: str | None = None,
        carrier_name: str | None = None,
        supplier_code: str | None = None,
        country_code: str | None = None,
        minimum_severity: RiskSeverity = "low",
        event_types: Iterable[EventType] | None = None,
        limit: int = 10,
    ) -> list[DisruptionEvent]:
        if not 1 <= limit <= 25:
            raise ValueError("limit must be between 1 and 25")

        allowed_types = set(event_types) if event_types is not None else None
        minimum_rank = SEVERITY_RANK[minimum_severity]
        filters = [location_query, carrier_name, supplier_code, country_code]
        normalized_filters = [
            value.strip().casefold() for value in filters if value and value.strip()
        ]

        matches = []
        for event in self._events:
            if not event.active_on(active_on):
                continue
            if SEVERITY_RANK[event.severity] < minimum_rank:
                continue
            if allowed_types is not None and event.event_type not in allowed_types:
                continue
            searchable = event.searchable_text()
            if any(value not in searchable for value in normalized_filters):
                continue
            matches.append(event)

        matches.sort(
            key=lambda event: (
                -SEVERITY_RANK[event.severity],
                -event.confidence,
                event.event_id,
            )
        )
        return matches[:limit]

    def lane_status(
        self,
        origin: str,
        destination: str,
        *,
        active_on: date = RISK_FEED_AS_OF,
    ) -> LaneStatusResult:
        origin_term = origin.strip().casefold()
        destination_term = destination.strip().casefold()
        if not origin_term or not destination_term:
            raise ValueError("origin and destination are required")

        matches = [
            event
            for event in self._events
            if event.active_on(active_on)
            and (
                origin_term in event.searchable_text()
                or destination_term in event.searchable_text()
            )
        ]
        matches.sort(key=lambda event: -SEVERITY_RANK[event.severity])
        return LaneStatusResult(
            origin=origin,
            destination=destination,
            active_on=active_on,
            risk_level=self._highest_risk(matches),
            expected_delay_days_min=max(
                (event.expected_delay_days_min for event in matches),
                default=0,
            ),
            expected_delay_days_max=max(
                (event.expected_delay_days_max for event in matches),
                default=0,
            ),
            event_count=len(matches),
            events=matches,
            recommended_actions=list(dict.fromkeys(event.recommended_action for event in matches)),
        )

    def supplier_signals(
        self,
        supplier_code: str,
        *,
        active_on: date = RISK_FEED_AS_OF,
    ) -> SupplierSignalResult:
        normalized = supplier_code.strip().upper()
        if not normalized:
            raise ValueError("supplier_code is required")
        matches = self.search(
            active_on=active_on,
            supplier_code=normalized,
            minimum_severity="low",
            limit=25,
        )
        return SupplierSignalResult(
            supplier_code=normalized,
            active_on=active_on,
            risk_level=self._highest_risk(matches),
            event_count=len(matches),
            events=matches,
        )

    @staticmethod
    def _highest_risk(events: Sequence[DisruptionEvent]) -> RiskLevel:
        if not events:
            return "clear"
        return max(events, key=lambda event: SEVERITY_RANK[event.severity]).severity


def parse_feed_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("active_on must use YYYY-MM-DD format") from exc


def build_synthetic_risk_feed() -> RiskFeedRepository:
    """Build a small external feed correlated with the operational seed scenario."""

    raw_events = [
        {
            "event_id": "EXT-2026-001",
            "title": "Vancouver terminal labor disruption",
            "event_type": "port_disruption",
            "severity": "critical",
            "status": "active",
            "starts_on": "2026-06-27",
            "expected_resolution": "2026-07-06",
            "locations": [
                "Port of Vancouver",
                "Vancouver, Canada",
                "Kaohsiung to Vancouver lane",
            ],
            "port_codes": ["CAYVR", "TWKHH"],
            "country_codes": ["CA", "TW"],
            "carrier_names": ["BlueArc Logistics"],
            "supplier_codes": ["SUP-001"],
            "expected_delay_days_min": 5,
            "expected_delay_days_max": 9,
            "summary": (
                "Intermittent terminal closures are delaying unloading and onward rail "
                "transfers for inbound containers."
            ),
            "recommended_action": (
                "Confirm terminal availability and evaluate air freight for production-critical "
                "components."
            ),
            "confidence": 0.96,
        },
        {
            "event_id": "EXT-2026-002",
            "title": "Kaohsiung export-container backlog",
            "event_type": "port_disruption",
            "severity": "high",
            "status": "monitoring",
            "starts_on": "2026-06-28",
            "expected_resolution": "2026-07-03",
            "locations": ["Kaohsiung, Taiwan", "Port of Kaohsiung"],
            "port_codes": ["TWKHH"],
            "country_codes": ["TW"],
            "carrier_names": ["BlueArc Logistics"],
            "supplier_codes": ["SUP-001"],
            "expected_delay_days_min": 2,
            "expected_delay_days_max": 4,
            "summary": (
                "Export-container dwell time is elevated after several vessel schedule changes."
            ),
            "recommended_action": (
                "Verify container loading confirmation before promising recovery dates."
            ),
            "confidence": 0.88,
        },
        {
            "event_id": "EXT-2026-003",
            "title": "BlueArc trans-Pacific capacity advisory",
            "event_type": "carrier_advisory",
            "severity": "high",
            "status": "active",
            "starts_on": "2026-06-25",
            "expected_resolution": "2026-07-08",
            "locations": ["Trans-Pacific eastbound network"],
            "port_codes": ["TWKHH", "CAYVR"],
            "country_codes": ["TW", "CA"],
            "carrier_names": ["BlueArc Logistics"],
            "supplier_codes": ["SUP-001", "SUP-004"],
            "expected_delay_days_min": 2,
            "expected_delay_days_max": 6,
            "summary": (
                "Available recovery capacity is constrained on eastbound priority services."
            ),
            "recommended_action": (
                "Reserve recovery capacity before authorizing an expedited shipment."
            ),
            "confidence": 0.84,
        },
        {
            "event_id": "EXT-2026-004",
            "title": "Apex Circuits capacity watch",
            "event_type": "supplier_watchlist",
            "severity": "medium",
            "status": "monitoring",
            "starts_on": "2026-06-20",
            "expected_resolution": "2026-07-15",
            "locations": ["Taiwan"],
            "country_codes": ["TW"],
            "supplier_codes": ["SUP-001"],
            "expected_delay_days_min": 1,
            "expected_delay_days_max": 3,
            "summary": (
                "Reported production utilization is elevated, reducing short-notice replacement "
                "capacity."
            ),
            "recommended_action": (
                "Confirm available replacement quantity before relying on supplier recovery."
            ),
            "confidence": 0.73,
        },
        {
            "event_id": "EXT-2026-005",
            "title": "Northern Germany inland-water restriction",
            "event_type": "weather",
            "severity": "medium",
            "status": "active",
            "starts_on": "2026-06-29",
            "expected_resolution": "2026-07-04",
            "locations": ["Hamburg, Germany", "Northern Germany"],
            "port_codes": ["DEHAM"],
            "country_codes": ["DE"],
            "carrier_names": ["NorthStar Freight"],
            "supplier_codes": ["SUP-005"],
            "expected_delay_days_min": 1,
            "expected_delay_days_max": 3,
            "summary": "Low water levels are constraining feeder capacity into Hamburg terminals.",
            "recommended_action": (
                "Check rail capacity for Rhine Controls orders not yet dispatched."
            ),
            "confidence": 0.79,
        },
        {
            "event_id": "EXT-2026-006",
            "title": "Mexico customs-document validation advisory",
            "event_type": "trade_compliance",
            "severity": "medium",
            "status": "active",
            "starts_on": "2026-06-26",
            "expected_resolution": "2026-07-10",
            "locations": ["Mexico", "United States border crossings"],
            "country_codes": ["MX", "US"],
            "supplier_codes": ["SUP-009"],
            "expected_delay_days_min": 1,
            "expected_delay_days_max": 2,
            "summary": (
                "Additional certificate and tariff-code validation is increasing document-review "
                "times."
            ),
            "recommended_action": "Validate certificates and tariff codes before dispatch.",
            "confidence": 0.91,
        },
        {
            "event_id": "EXT-2026-007",
            "title": "Severe weather near Tokyo cargo gateways",
            "event_type": "weather",
            "severity": "high",
            "status": "monitoring",
            "starts_on": "2026-06-30",
            "expected_resolution": "2026-07-02",
            "locations": ["Tokyo, Japan", "Narita cargo gateway"],
            "port_codes": ["JPTYO"],
            "country_codes": ["JP"],
            "carrier_names": ["Continental Air Cargo"],
            "supplier_codes": ["SUP-004"],
            "expected_delay_days_min": 1,
            "expected_delay_days_max": 3,
            "summary": "Weather restrictions may interrupt cargo handling and departure slots.",
            "recommended_action": "Check booked uplift before changing the operational ETA.",
            "confidence": 0.82,
        },
        {
            "event_id": "EXT-2026-008",
            "title": "Chicago intermodal transfer congestion",
            "event_type": "carrier_advisory",
            "severity": "low",
            "status": "monitoring",
            "starts_on": "2026-06-29",
            "expected_resolution": "2026-07-02",
            "locations": ["Chicago, United States", "Chicago intermodal terminals"],
            "country_codes": ["US"],
            "carrier_names": ["NorthStar Freight"],
            "supplier_codes": ["SUP-003", "SUP-011"],
            "expected_delay_days_min": 0,
            "expected_delay_days_max": 1,
            "summary": "Intermodal transfer dwell time is modestly above the normal range.",
            "recommended_action": (
                "Monitor priority arrivals; no rerouting is currently recommended."
            ),
            "confidence": 0.68,
        },
    ]
    events = [
        DisruptionEvent(
            **item,
            reference=f"external-risk:{item['event_id']}",
        )
        for item in raw_events
    ]
    return RiskFeedRepository(events)
