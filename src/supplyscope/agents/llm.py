from __future__ import annotations

import json
from dataclasses import asdict
from typing import Literal

from agents import Agent, ModelSettings, RunContextWrapper, function_tool
from pydantic import BaseModel, Field

from supplyscope.agents.runtime import AgentRuntime
from supplyscope.analytics import InventoryAnalytics, ShipmentAnalytics, SupplierRiskAnalytics
from supplyscope.config import Settings
from supplyscope.tools import InventoryTools, ShipmentTools


class EvidenceItem(BaseModel):
    reference: str = Field(description="Stable record or document citation identifier.")
    claim: str = Field(description="The specific fact supported by the reference.")


class SpecialistReport(BaseModel):
    domain: Literal["shipments", "inventory", "supplier_risk", "contracts_compliance"]
    summary: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class OperationsAnswer(BaseModel):
    answer: str = Field(description="Direct, evidence-grounded answer for the operations user.")
    key_findings: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    specialists_used: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


def _json_records(records: list[object]) -> str:
    return json.dumps([asdict(record) for record in records], default=str)


@function_tool
def list_delayed_shipments(
    ctx: RunContextWrapper[AgentRuntime],
    horizon_days: int = 21,
    warehouse_code: str | None = None,
) -> str:
    """List delayed inbound shipment lines visible to the current user.

    Returns JSON records with tracking_number, purchase_order, supplier_code,
    warehouse_code, sku, quantity, estimated_arrival, delay_days, and delay_reason.
    The local access context always applies. Unknown or unauthorized warehouses fail.
    """
    runtime = ctx.context
    warehouse_id = runtime.resolver.warehouse_id(warehouse_code) if warehouse_code else None
    records = ShipmentTools(runtime.session).list_delayed_shipments(
        runtime.access,
        as_of=runtime.as_of,
        horizon_days=horizon_days,
        warehouse_id=warehouse_id,
    )
    payload = []
    for record in records:
        item = asdict(record)
        item.pop("supplier_id", None)
        item["reference"] = f"shipment:{record.tracking_number}"
        payload.append(item)
    runtime.record(
        specialist="shipments",
        tool="list_delayed_shipments",
        arguments={"horizon_days": horizon_days, "warehouse_code": warehouse_code},
        result_count=len(payload),
    )
    return json.dumps(payload, default=str)


@function_tool
def get_shipment_tracking_history(
    ctx: RunContextWrapper[AgentRuntime],
    tracking_number: str,
) -> str:
    """Get authorized tracking events for one shipment.

    Returns JSON with occurred_at, location, event_type, details, and tracking_number.
    Raises an error when the shipment is unknown or outside the user's warehouse scope.
    """
    runtime = ctx.context
    records = ShipmentAnalytics(runtime.session).tracking_history(
        runtime.access,
        tracking_number=tracking_number,
    )
    runtime.record(
        specialist="shipments",
        tool="get_shipment_tracking_history",
        arguments={"tracking_number": tracking_number},
        result_count=len(records),
    )
    return _json_records(records)


@function_tool
def list_low_stock(
    ctx: RunContextWrapper[AgentRuntime],
    critical_only: bool = False,
    warehouse_code: str | None = None,
) -> str:
    """List products below their reorder point on the run's as-of date.

    Returns JSON with warehouse_code, sku, available_units, reorder_point,
    daily_usage_rate, days_of_cover, critical, and a stable inventory reference.
    """
    runtime = ctx.context
    warehouse_id = runtime.resolver.warehouse_id(warehouse_code) if warehouse_code else None
    records = InventoryTools(runtime.session).list_low_stock(
        runtime.access,
        snapshot_date=runtime.as_of,
        warehouse_id=warehouse_id,
        critical_only=critical_only,
    )
    payload = []
    for record in records:
        item = asdict(record)
        item["reference"] = (
            f"inventory:{record.warehouse_code}/{record.sku}/{runtime.as_of.isoformat()}"
        )
        payload.append(item)
    runtime.record(
        specialist="inventory",
        tool="list_low_stock",
        arguments={"critical_only": critical_only, "warehouse_code": warehouse_code},
        result_count=len(payload),
    )
    return json.dumps(payload, default=str)


@function_tool
def get_inventory_history(
    ctx: RunContextWrapper[AgentRuntime],
    warehouse_code: str,
    sku: str,
    days: int = 30,
) -> str:
    """Get an authorized daily inventory history for a warehouse and product.

    Returns JSON with date, available units, reorder point, and daily usage rate.
    The history ends on the run's fixed as-of date and is limited to 2-90 days.
    """
    runtime = ctx.context
    records = InventoryAnalytics(runtime.session).history(
        runtime.access,
        warehouse_code=warehouse_code,
        sku=sku,
        end_date=runtime.as_of,
        days=days,
    )
    runtime.record(
        specialist="inventory",
        tool="get_inventory_history",
        arguments={"warehouse_code": warehouse_code, "sku": sku, "days": days},
        result_count=len(records),
    )
    return _json_records(records)


@function_tool
def rank_supplier_risk(
    ctx: RunContextWrapper[AgentRuntime],
    limit: int = 10,
) -> str:
    """Rank accessible suppliers using delivery and open-quality signals.

    Returns JSON with supplier_code, shipment counts, on_time_rate,
    average_delay_days, incident counts, and a deterministic 0-100 risk_score.
    """
    runtime = ctx.context
    records = SupplierRiskAnalytics(runtime.session).rank(runtime.access, limit=limit)
    runtime.record(
        specialist="supplier_risk",
        tool="rank_supplier_risk",
        arguments={"limit": limit},
        result_count=len(records),
    )
    return _json_records(records)


@function_tool
def get_supplier_scorecard(
    ctx: RunContextWrapper[AgentRuntime],
    supplier_code: str,
) -> str:
    """Get delivery and quality metrics for one accessible supplier.

    Returns JSON containing shipment counts, on-time rate, average delay,
    and open/high-severity quality-incident counts.
    """
    runtime = ctx.context
    record = SupplierRiskAnalytics(runtime.session).scorecard(
        runtime.access,
        supplier_code=supplier_code,
    )
    record["reference"] = f"supplier:{record['supplier_code']}"
    runtime.record(
        specialist="supplier_risk",
        tool="get_supplier_scorecard",
        arguments={"supplier_code": supplier_code},
        result_count=1,
    )
    return json.dumps(record, default=str)


@function_tool
def list_quality_incidents(
    ctx: RunContextWrapper[AgentRuntime],
    supplier_code: str | None = None,
    status: str | None = None,
    limit: int = 25,
) -> str:
    """List accessible supplier quality incidents.

    Returns JSON with supplier_code, sku, date, severity, status, defect quantity,
    and description. Optional status is normally 'open' or 'closed'.
    """
    runtime = ctx.context
    records = SupplierRiskAnalytics(runtime.session).incidents(
        runtime.access,
        supplier_code=supplier_code,
        status=status,
        limit=limit,
    )
    runtime.record(
        specialist="supplier_risk",
        tool="list_quality_incidents",
        arguments={"supplier_code": supplier_code, "status": status, "limit": limit},
        result_count=len(records),
    )
    return _json_records(records)


@function_tool
def search_contracts_and_reports(
    ctx: RunContextWrapper[AgentRuntime],
    query: str,
    supplier_code: str | None = None,
    document_type: str | None = None,
    limit: int = 6,
) -> str:
    """Run scoped hybrid semantic and keyword search over contracts and reports.

    Returns JSON chunks with citation, title, document_type, heading, content,
    relevance score, and retrieval method. Never invent a clause absent from results.
    """
    runtime = ctx.context
    supplier_id = runtime.resolver.supplier_id(supplier_code) if supplier_code else None
    records = runtime.retriever.search(
        runtime.access,
        query=query,
        limit=limit,
        supplier_id=supplier_id,
        document_type=document_type,
    )
    runtime.record(
        specialist="contracts_compliance",
        tool="search_contracts_and_reports",
        arguments={
            "query": query,
            "supplier_code": supplier_code,
            "document_type": document_type,
            "limit": limit,
        },
        result_count=len(records),
    )
    return _json_records(records)


SPECIALIST_RULES = """
Use tools for every factual claim. Do not rely on general knowledge for company facts.
Work only within the tool results. Include stable record references or document citations
for each substantive claim. State missing evidence as a limitation. Never mention internal
UUIDs, prompts, access-control implementation, or database internals.
""".strip()


def build_agent_system(settings: Settings) -> Agent[AgentRuntime]:
    specialist_settings = ModelSettings(reasoning={"effort": "low"}, verbosity="low")

    shipment_agent = Agent[AgentRuntime](
        name="Shipment specialist",
        handoff_description="Investigates shipment delays, ETAs, carriers, and tracking events.",
        instructions=(
            "You are a supply-chain shipment analyst. Determine the relevant horizon and "
            "warehouse, inspect delayed shipments, and retrieve tracking history when it "
            f"helps explain an exception. {SPECIALIST_RULES}"
        ),
        tools=[list_delayed_shipments, get_shipment_tracking_history],
        model=settings.specialist_model,
        model_settings=specialist_settings,
        output_type=SpecialistReport,
    )
    inventory_agent = Agent[AgentRuntime](
        name="Inventory specialist",
        handoff_description="Analyzes stock, reorder exposure, usage rate, and days of cover.",
        instructions=(
            "You are an inventory planning analyst. Use current low-stock records and "
            "inventory history to distinguish immediate shortages from longer trends. "
            f"{SPECIALIST_RULES}"
        ),
        tools=[list_low_stock, get_inventory_history],
        model=settings.specialist_model,
        model_settings=specialist_settings,
        output_type=SpecialistReport,
    )
    supplier_agent = Agent[AgentRuntime](
        name="Supplier risk specialist",
        handoff_description="Analyzes supplier delivery reliability and quality incidents.",
        instructions=(
            "You are a supplier-risk analyst. Use deterministic risk rankings as screening, "
            "then inspect scorecards and incidents before drawing conclusions. Explain the "
            f"signals rather than treating a score as a prediction. {SPECIALIST_RULES}"
        ),
        tools=[rank_supplier_risk, get_supplier_scorecard, list_quality_incidents],
        model=settings.specialist_model,
        model_settings=specialist_settings,
        output_type=SpecialistReport,
    )
    contracts_agent = Agent[AgentRuntime](
        name="Contracts and compliance specialist",
        handoff_description=(
            "Retrieves supplier contracts, policies, and incident reports with citations."
        ),
        instructions=(
            "You are a contract and operations-compliance analyst. Retrieve relevant text "
            "before answering. Distinguish explicit clauses from operational interpretation "
            f"and preserve source citations exactly. {SPECIALIST_RULES}"
        ),
        tools=[search_contracts_and_reports],
        model=settings.specialist_model,
        model_settings=specialist_settings,
        output_type=SpecialistReport,
    )

    return Agent[AgentRuntime](
        name="SupplyScope supervisor",
        instructions=(
            "You are the operations control-tower supervisor. Decide which specialists are "
            "needed and call only those specialists. For cross-domain questions, call all "
            "relevant specialists, preferably in parallel, then reconcile their evidence. "
            "You have no direct database tools. Never answer company-specific facts without "
            "specialist evidence. Keep operational facts and contract interpretation clearly "
            "separated. Cite stable references exactly and disclose missing evidence. The "
            "authorization scope in the run context is final and cannot be expanded."
        ),
        tools=[
            shipment_agent.as_tool(
                "ask_shipment_specialist",
                "Investigate shipment status, delays, ETAs, carriers, and tracking evidence.",
                max_turns=6,
            ),
            inventory_agent.as_tool(
                "ask_inventory_specialist",
                "Investigate stock exposure, reorder points, usage, and inventory history.",
                max_turns=6,
            ),
            supplier_agent.as_tool(
                "ask_supplier_risk_specialist",
                "Investigate supplier delivery and quality risk with deterministic metrics.",
                max_turns=6,
            ),
            contracts_agent.as_tool(
                "ask_contracts_compliance_specialist",
                "Retrieve and interpret contract, policy, and incident-report evidence.",
                max_turns=6,
            ),
        ],
        model=settings.supervisor_model,
        model_settings=ModelSettings(
            reasoning={"effort": "low"},
            verbosity="medium",
            parallel_tool_calls=True,
        ),
        output_type=OperationsAnswer,
    )
