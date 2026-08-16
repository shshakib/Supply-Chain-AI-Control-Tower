from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import date

from agents import Runner
from pydantic import ValidationError
from sqlalchemy.orm import Session

from control_tower.access import AccessContext
from control_tower.agents.llm import SUPERVISOR_MAX_TURNS, OperationsAnswer, build_agent_system
from control_tower.agents.runtime import AgentRuntime, ToolEvent
from control_tower.config import Settings
from control_tower.conversations import ConversationMessage
from control_tower.embeddings import OpenAIEmbeddingProvider
from control_tower.integrations.risk_mcp_client import RiskMCPConnector
from control_tower.observability import ExecutionTrace
from control_tower.retrieval import HybridDocumentRetriever


class MissingOpenAIConfiguration(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentRunResponse:
    output: OperationsAnswer
    tool_events: list[ToolEvent]
    response_id: str | None
    integrations: dict[str, dict[str, object]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "output": self.output.model_dump(mode="json"),
            "tool_events": [asdict(event) for event in self.tool_events],
            "response_id": self.response_id,
            "integrations": self.integrations,
        }


class AgentService:
    def __init__(
        self,
        settings: Settings,
        *,
        risk_mcp: RiskMCPConnector | None = None,
    ) -> None:
        self.settings = settings
        self.risk_mcp = risk_mcp or RiskMCPConnector(
            enabled=settings.risk_mcp_enabled,
            url=settings.risk_mcp_url,
            connect_timeout_seconds=settings.risk_mcp_connect_timeout_seconds,
        )
        self.supervisor = build_agent_system(settings)
        self._started = False

    @property
    def mcp_status(self) -> dict[str, object]:
        return self.risk_mcp.status.to_dict()

    async def start(self) -> None:
        if self._started:
            return
        await self.risk_mcp.connect()
        self.supervisor = build_agent_system(
            self.settings,
            risk_mcp_server=self.risk_mcp.server,
        )
        self._started = True

    async def close(self) -> None:
        await self.risk_mcp.close()
        self._started = False

    async def __aenter__(self) -> AgentService:
        await self.start()
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
        await self.close()

    async def ask(
        self,
        session: Session,
        access: AccessContext,
        *,
        question: str,
        as_of: date,
        history: list[ConversationMessage] | None = None,
        trace: ExecutionTrace | None = None,
    ) -> AgentRunResponse:
        if not os.getenv("OPENAI_API_KEY"):
            raise MissingOpenAIConfiguration(
                "OPENAI_API_KEY is not configured. Add it to .env before using LLM chat."
            )
        await self.start()
        question = question.strip()
        if not question:
            raise ValueError("question cannot be empty")
        if len(question) > 4000:
            raise ValueError("question cannot exceed 4000 characters")

        embedding_provider = OpenAIEmbeddingProvider(
            model=self.settings.embedding_model,
            dimensions=self.settings.embedding_dimensions,
        )
        runtime = AgentRuntime(
            session=session,
            access=access,
            as_of=as_of,
            retriever=HybridDocumentRetriever(session, embedding_provider),
            trace=trace,
        )
        prompt = self._build_prompt(question, runtime, history or [])
        try:
            result = await Runner.run(
                self.supervisor,
                prompt,
                context=runtime,
                max_turns=SUPERVISOR_MAX_TURNS,
            )
        except Exception:
            if trace is not None:
                trace.fail_open_operations("Agent run failed")
            raise
        output = self._coerce_output(result.final_output)
        if trace is not None:
            trace.start(
                event_type="answer",
                node="answer",
                label="Preparing cited answer",
                parent_node="review" if trace.was_started("review") else "supervisor",
                source="application",
            )
            trace.complete(
                node="answer",
                label="Answer ready",
                details={
                    "citation_count": len(output.citations),
                    "specialists_used": output.specialists_used,
                },
            )
            for node, label in {
                "shipments": "Shipment specialist not needed",
                "inventory": "Inventory specialist not needed",
                "supplier_risk": "Supplier-risk specialist not needed",
                "contracts_compliance": "Contracts specialist not needed",
                "postgresql": "No operational query needed",
                "pgvector": "No document retrieval needed",
                "mcp": "No external-risk lookup needed",
            }.items():
                trace.skip(node=node, label=label)
        return AgentRunResponse(
            output=output,
            tool_events=list(runtime.events),
            response_id=getattr(result, "last_response_id", None),
            integrations={"external_risk_mcp": self.mcp_status},
        )

    @staticmethod
    def _build_prompt(
        question: str,
        runtime: AgentRuntime,
        history: list[ConversationMessage],
    ) -> str:
        history_text = "\n".join(
            f"{message.role.title()}: {message.content}" for message in history[-10:]
        )
        if not history_text:
            history_text = "No previous conversation messages."
        warehouse_codes = ", ".join(runtime.warehouse_codes()) or "none"
        return (
            "Answer the current operations question using specialist evidence.\n\n"
            f"As-of date: {runtime.as_of.isoformat()}\n"
            f"Authorized organization: {runtime.access.organization_slug}\n"
            f"Authorized warehouse labels: {warehouse_codes}\n"
            "These labels describe scope only; tools enforce authorization independently.\n\n"
            f"Recent conversation:\n{history_text}\n\n"
            f"Current question:\n{question}"
        )

    @staticmethod
    def _coerce_output(value: object) -> OperationsAnswer:
        if isinstance(value, OperationsAnswer):
            return value
        if isinstance(value, str):
            try:
                return OperationsAnswer.model_validate_json(value)
            except ValidationError:
                return OperationsAnswer(answer=value)
        return OperationsAnswer.model_validate(value)
