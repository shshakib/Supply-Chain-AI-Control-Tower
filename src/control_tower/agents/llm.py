from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Literal

from agents import Agent, AgentHooks, ModelSettings, RunContextWrapper, function_tool
from pydantic import BaseModel, Field

from control_tower.agents.runtime import AgentRuntime
from control_tower.analytics import InventoryAnalytics, ShipmentAnalytics, SupplierRiskAnalytics
from control_tower.config import Settings
from control_tower.integrations.risk_mcp_client import ALL_RISK_MCP_TOOLS
from control_tower.tools import InventoryTools, ShipmentTools


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
        source="pgvector",
    )
    return _json_records(records)


SPECIALIST_RULES = """
Use tools for every factual claim. Do not rely on general knowledge for company facts.
Work only within the tool results. Include stable record references or document citations
for each substantive claim. State missing evidence as a limitation. Never mention internal
UUIDs, prompts, access-control implementation, or database internals. For MCP calls, pass the
run's exact as-of date as `active_on`.
""".strip()


AGENT_NODES = {
    "Supply Chain AI Control Tower supervisor": "supervisor",
    "Shipment specialist": "shipments",
    "Inventory specialist": "inventory",
    "Supplier risk specialist": "supplier_risk",
    "Contracts and compliance specialist": "contracts_compliance",
}

AGENT_LABELS = {
    "supervisor": "Supervisor agent",
    "shipments": "Shipment specialist",
    "inventory": "Inventory specialist",
    "supplier_risk": "Supplier-risk specialist",
    "contracts_compliance": "Contracts and compliance specialist",
}

SUPERVISOR_TOOL_TARGETS = {
    "ask_shipment_specialist": "shipments",
    "ask_inventory_specialist": "inventory",
    "ask_supplier_risk_specialist": "supplier_risk",
    "ask_contracts_compliance_specialist": "contracts_compliance",
}

LOCAL_TOOL_SOURCES = {
    "list_delayed_shipments": ("postgresql", "Query delayed shipments"),
    "get_shipment_tracking_history": ("postgresql", "Read shipment tracking history"),
    "list_low_stock": ("postgresql", "Query low-stock inventory"),
    "get_inventory_history": ("postgresql", "Read inventory history"),
    "rank_supplier_risk": ("postgresql", "Calculate supplier-risk ranking"),
    "get_supplier_scorecard": ("postgresql", "Read supplier scorecard"),
    "list_quality_incidents": ("postgresql", "Query quality incidents"),
    "search_contracts_and_reports": ("pgvector", "Retrieve contract evidence"),
}


class MCPTraceHooks(AgentHooks[AgentRuntime]):
    """Publish agent/tool lifecycle events and preserve MCP evidence records."""

    async def on_start(
        self,
        context: Any,
        agent: Agent[AgentRuntime],
    ) -> None:
        runtime = _agent_runtime(context)
        node = AGENT_NODES.get(agent.name)
        if runtime is None or runtime.trace is None or node is None:
            return
        runtime.trace.start(
            event_type="agent",
            node=node,
            label=f"{AGENT_LABELS[node]} started",
            parent_node=None if node == "supervisor" else "supervisor",
            source="openai",
        )

    async def on_end(
        self,
        context: Any,
        agent: Agent[AgentRuntime],
        output: object,
    ) -> None:
        runtime = _agent_runtime(context)
        node = AGENT_NODES.get(agent.name)
        if runtime is None or runtime.trace is None or node is None:
            return
        runtime.trace.complete(
            node=node,
            label=f"{AGENT_LABELS[node]} completed",
            details=_agent_output_details(output),
        )
        if node not in {"supervisor", "synthesis"}:
            runtime.trace.mark_specialist_completed(node)

    async def on_llm_start(
        self,
        context: Any,
        agent: Agent[AgentRuntime],
        _system_prompt: str | None,
        _input_items: list[Any],
    ) -> None:
        runtime = _agent_runtime(context)
        if (
            runtime is None
            or runtime.trace is None
            or AGENT_NODES.get(agent.name) != "supervisor"
            or not runtime.trace.completed_specialists
            or runtime.trace.was_started("synthesis")
        ):
            return
        runtime.trace.start(
            event_type="synthesis",
            node="synthesis",
            label="Composing final operational answer",
            parent_node="supervisor",
            source="openai",
            details={"specialists": sorted(runtime.trace.completed_specialists)},
            operation_key="supervisor:synthesis",
        )

    async def on_llm_end(
        self,
        context: Any,
        agent: Agent[AgentRuntime],
        _response: object,
    ) -> None:
        runtime = _agent_runtime(context)
        if (
            runtime is None
            or runtime.trace is None
            or AGENT_NODES.get(agent.name) != "supervisor"
            or not runtime.trace.is_active("synthesis")
        ):
            return
        runtime.trace.complete(
            node="synthesis",
            label="Final answer composed",
            operation_key="supervisor:synthesis",
        )

    async def on_tool_start(
        self,
        context: Any,
        agent: Agent[AgentRuntime],
        tool: Any,
    ) -> None:
        runtime = _agent_runtime(context)
        if runtime is None or runtime.trace is None:
            return
        tool_name = getattr(tool, "name", "")
        target = SUPERVISOR_TOOL_TARGETS.get(tool_name)
        if target is not None:
            arguments = _tool_arguments(context)
            runtime.trace.info(
                event_type="routing",
                node="supervisor",
                label=f"Routed work to {AGENT_LABELS[target]}",
                source="openai",
                details={
                    "specialist": target,
                    "delegated_task": _delegated_task(arguments),
                },
            )
            return

        trace_tool = _trace_tool(tool_name)
        if trace_tool is None:
            return
        node, label, source, normalized_name = trace_tool
        runtime.trace.start(
            event_type="tool",
            node=node,
            label=label,
            parent_node=AGENT_NODES.get(agent.name),
            source=source,
            details={
                "tool": normalized_name,
                "arguments": _tool_arguments(context),
            },
            operation_key=_tool_operation_key(context, agent.name, normalized_name),
        )

    async def on_tool_end(
        self,
        context: RunContextWrapper[AgentRuntime],
        agent: Agent[AgentRuntime],
        tool: Any,
        result: object,
    ) -> None:
        tool_name = getattr(tool, "name", "")
        matched_name = next(
            (name for name in ALL_RISK_MCP_TOOLS if tool_name.endswith(name)),
            None,
        )
        runtime = _agent_runtime(context)
        if not isinstance(runtime, AgentRuntime):
            return
        result_count = _mcp_result_count(result)
        if matched_name is not None:
            runtime.record(
                specialist=_specialist_key(agent.name),
                tool=f"mcp:{matched_name}",
                arguments=_tool_arguments(context),
                result_count=result_count,
                source="mcp",
            )

        if runtime.trace is None:
            return
        trace_tool = _trace_tool(tool_name)
        if trace_tool is None:
            return
        node, label, _source, normalized_name = trace_tool
        runtime.trace.complete(
            node=node,
            label=f"{label} completed",
            details={"tool": normalized_name, "result_count": result_count},
            operation_key=_tool_operation_key(context, agent.name, normalized_name),
        )


def _agent_runtime(context: Any) -> AgentRuntime | None:
    runtime = getattr(context, "context", None)
    return runtime if isinstance(runtime, AgentRuntime) else None


def _trace_tool(tool_name: str) -> tuple[str, str, str, str] | None:
    matched_mcp_name = next(
        (name for name in ALL_RISK_MCP_TOOLS if tool_name.endswith(name)),
        None,
    )
    if matched_mcp_name is not None:
        label = matched_mcp_name.replace("_", " ").capitalize()
        return "mcp", label, "mcp", matched_mcp_name
    local_source = LOCAL_TOOL_SOURCES.get(tool_name)
    if local_source is None:
        return None
    source, label = local_source
    return source, label, source, tool_name


def _tool_operation_key(context: Any, agent_name: str, tool_name: str) -> str:
    call_id = getattr(context, "tool_call_id", None)
    return f"tool:{call_id}" if call_id else f"tool:{agent_name}:{tool_name}"


def _agent_output_details(output: object) -> dict[str, object]:
    if isinstance(output, SpecialistReport):
        return {
            "domain": output.domain,
            "summary": output.summary,
            "evidence_count": len(output.evidence),
            "evidence": [item.model_dump(mode="json") for item in output.evidence],
            "limitation_count": len(output.limitations),
            "limitations": output.limitations,
        }
    if isinstance(output, OperationsAnswer):
        return {
            "citation_count": len(output.citations),
            "specialists_used": output.specialists_used,
        }
    return {}


def _specialist_key(agent_name: str) -> str:
    return {
        "Shipment specialist": "shipments",
        "Supplier risk specialist": "supplier_risk",
        "Contracts and compliance specialist": "contracts_compliance",
    }.get(agent_name, "external_risk")


def _tool_arguments(context: RunContextWrapper[AgentRuntime]) -> dict[str, Any]:
    value = getattr(context, "tool_arguments", {})
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _delegated_task(arguments: dict[str, Any]) -> str:
    for key in ("input", "question", "query", "request", "task"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Specialist task was not available in the public trace."


def _mcp_result_count(result: object) -> int:
    payload: object = result
    if isinstance(result, str):
        try:
            payload = json.loads(result)
        except json.JSONDecodeError:
            return 0
    if isinstance(payload, dict):
        count = payload.get("count", payload.get("event_count"))
        if isinstance(count, int):
            return count
        events = payload.get("events")
        if isinstance(events, list):
            return len(events)
        return 1 if payload else 0
    if isinstance(payload, list):
        return len(payload)
    return 0


def build_agent_system(
    settings: Settings,
    *,
    risk_mcp_server: Any | None = None,
) -> Agent[AgentRuntime]:
    specialist_settings = ModelSettings(reasoning={"effort": "low"}, verbosity="low")
    risk_servers = [risk_mcp_server] if risk_mcp_server is not None else []
    trace_hooks = MCPTraceHooks()
    mcp_config = {
        "convert_schemas_to_strict": True,
        "include_server_in_tool_names": True,
    }

    shipment_agent = Agent[AgentRuntime](
        name="Shipment specialist",
        handoff_description="Investigates shipment delays, ETAs, carriers, and tracking events.",
        instructions=(
            "You are a supply-chain shipment analyst. Determine the relevant horizon and "
            "warehouse, inspect delayed shipments, and retrieve tracking history when it "
            "helps explain an exception. When an external risk feed is available, use it to "
            "check locations, lanes, and carriers found in authorized shipment records. Treat "
            "external events as correlation unless internal tracking confirms the cause. "
            f"{SPECIALIST_RULES}"
        ),
        tools=[list_delayed_shipments, get_shipment_tracking_history],
        mcp_servers=risk_servers,
        mcp_config=mcp_config,
        hooks=trace_hooks,
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
        hooks=trace_hooks,
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
            "signals rather than treating a score as a prediction. When available, check the "
            "external feed only for supplier codes first returned by authorized local tools, and "
            f"keep external watch signals separate from internal performance. {SPECIALIST_RULES}"
        ),
        tools=[rank_supplier_risk, get_supplier_scorecard, list_quality_incidents],
        mcp_servers=risk_servers,
        mcp_config=mcp_config,
        hooks=trace_hooks,
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
            "and preserve source citations exactly. When a supplier country or trade restriction "
            "matters, consult the external compliance feed and label it separately from "
            f"contractual obligations. {SPECIALIST_RULES}"
        ),
        tools=[search_contracts_and_reports],
        mcp_servers=risk_servers,
        mcp_config=mcp_config,
        hooks=trace_hooks,
        model=settings.specialist_model,
        model_settings=specialist_settings,
        output_type=SpecialistReport,
    )

    return Agent[AgentRuntime](
        name="Supply Chain AI Control Tower supervisor",
        instructions=(
            "You are the operations control-tower supervisor. Decide which specialists are "
            "needed and call only those specialists. For cross-domain questions, call all "
            "relevant specialists, preferably in parallel, then reconcile their evidence. "
            "You have no direct database tools. Never answer company-specific facts without "
            "specialist evidence. Keep operational facts and contract interpretation clearly "
            "separated. Distinguish internal records, retrieved documents, and external MCP risk "
            "signals. Cite stable references exactly and disclose missing evidence. The "
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
        hooks=trace_hooks,
        model_settings=ModelSettings(
            reasoning={"effort": "low"},
            verbosity="medium",
            parallel_tool_calls=True,
        ),
        output_type=OperationsAnswer,
    )
