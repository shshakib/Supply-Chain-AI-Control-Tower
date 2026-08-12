from __future__ import annotations

from datetime import date

from control_tower.integrations.risk_feed import (
    RISK_FEED_AS_OF,
    build_synthetic_risk_feed,
)


def test_vancouver_disruption_matches_seeded_critical_scenario() -> None:
    repository = build_synthetic_risk_feed()

    events = repository.search(
        active_on=RISK_FEED_AS_OF,
        location_query="Vancouver",
        carrier_name="BlueArc Logistics",
        supplier_code="SUP-001",
    )

    assert [event.event_id for event in events] == ["EXT-2026-001"]
    assert events[0].reference == "external-risk:EXT-2026-001"
    assert events[0].synthetic is True


def test_supplier_signals_rank_external_events_without_internal_data() -> None:
    result = build_synthetic_risk_feed().supplier_signals("sup-001")

    assert result.supplier_code == "SUP-001"
    assert result.risk_level == "critical"
    assert result.event_count == 4


def test_resolved_date_excludes_old_events() -> None:
    events = build_synthetic_risk_feed().search(
        active_on=date(2026, 8, 1),
        location_query="Vancouver",
    )

    assert events == []


def test_lane_status_returns_clear_when_no_external_signal_matches() -> None:
    result = build_synthetic_risk_feed().lane_status(
        "Sydney",
        "Auckland",
        active_on=RISK_FEED_AS_OF,
    )

    assert result.risk_level == "clear"
    assert result.event_count == 0
    assert result.expected_delay_days_max == 0
