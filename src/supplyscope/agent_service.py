from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import date

from agents import Runner
from pydantic import ValidationError
from sqlalchemy.orm import Session

from supplyscope.access import AccessContext
from supplyscope.agents.llm import OperationsAnswer, build_agent_system
from supplyscope.agents.runtime import AgentRuntime, ToolEvent
from supplyscope.config import Settings
from supplyscope.conversations import ConversationMessage
from supplyscope.embeddings import OpenAIEmbeddingProvider
from supplyscope.retrieval import HybridDocumentRetriever


class MissingOpenAIConfiguration(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentRunResponse:
    output: OperationsAnswer
    tool_events: list[ToolEvent]
    response_id: str | None

    def to_dict(self) -> dict:
        return {
            "output": self.output.model_dump(mode="json"),
            "tool_events": [asdict(event) for event in self.tool_events],
            "response_id": self.response_id,
        }


class AgentService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.supervisor = build_agent_system(settings)

    async def ask(
        self,
        session: Session,
        access: AccessContext,
        *,
        question: str,
        as_of: date,
        history: list[ConversationMessage] | None = None,
    ) -> AgentRunResponse:
        if not os.getenv("OPENAI_API_KEY"):
            raise MissingOpenAIConfiguration(
                "OPENAI_API_KEY is not configured. Add it to .env before using LLM chat."
            )
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
        )
        prompt = self._build_prompt(question, runtime, history or [])
        result = await Runner.run(
            self.supervisor,
            prompt,
            context=runtime,
            max_turns=14,
        )
        output = self._coerce_output(result.final_output)
        return AgentRunResponse(
            output=output,
            tool_events=list(runtime.events),
            response_id=getattr(result, "last_response_id", None),
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
