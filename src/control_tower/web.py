from __future__ import annotations

import argparse
import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from datetime import date
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import Engine, func, select

from control_tower.access import AccessDeniedError, AccessService
from control_tower.agent_service import AgentService, MissingOpenAIConfiguration
from control_tower.agents.specialists import (
    DocumentSpecialist,
    InventorySpecialist,
    ShipmentSpecialist,
)
from control_tower.agents.supervisor import SupplyRiskSupervisor
from control_tower.config import Settings, get_settings
from control_tower.conversations import ConversationService
from control_tower.database import create_database_engine, create_schema, session_scope
from control_tower.models import DocumentChunk, Membership, Organization, User
from control_tower.observability import ExecutionEvent, ExecutionTrace
from control_tower.synthetic import DEMO_AS_OF
from control_tower.tools import DocumentTools, InventoryTools, ShipmentTools

STATIC_DIR = Path(__file__).parent / "static"
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    user_email: str
    conversation_id: uuid.UUID | None = None
    as_of: date = DEMO_AS_OF


class DemoRequest(BaseModel):
    user_email: str = "noah.east@controltower.demo"
    question: str = (
        "Which delayed shipments could stop production, and do the responsible supplier "
        "contracts include late-delivery remedies?"
    )
    as_of: date = DEMO_AS_OF


def _sse_frame(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


def _trace_citations(trace: ExecutionTrace) -> list[str]:
    citations: list[str] = []
    for event in trace.events:
        evidence = (event.details or {}).get("evidence", [])
        if not isinstance(evidence, list):
            continue
        for item in evidence:
            if not isinstance(item, dict):
                continue
            reference = item.get("reference")
            if isinstance(reference, str) and reference not in citations:
                citations.append(reference)
    return citations


async def _stream_trace(
    operation: Callable[[ExecutionTrace], Awaitable[dict[str, object]]],
) -> AsyncIterator[str]:
    queue: asyncio.Queue[tuple[str, dict[str, object]] | None] = asyncio.Queue()

    def publish(event: ExecutionEvent) -> None:
        queue.put_nowait(("trace", event.to_dict()))

    trace = ExecutionTrace(sink=publish)

    async def execute() -> None:
        try:
            payload = await operation(trace)
        except HTTPException as exc:
            trace.fail_open_operations("Request failed")
            queue.put_nowait(("error", {"detail": str(exc.detail)}))
        except AccessDeniedError as exc:
            trace.fail_open_operations("Access denied")
            queue.put_nowait(("error", {"detail": str(exc)}))
        except Exception as exc:  # pragma: no cover - defensive transport boundary
            trace.fail_open_operations("Request failed")
            logger.exception("Streamed Control Tower request failed")
            queue.put_nowait(
                (
                    "error",
                    {"detail": f"The operation failed ({type(exc).__name__})."},
                )
            )
        else:
            queue.put_nowait(("result", payload))
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(execute())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=15)
            except TimeoutError:
                yield ": keep-alive\n\n"
                continue
            if item is None:
                break
            event_name, payload = item
            yield _sse_frame(event_name, payload)
    finally:
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def _streaming_response(
    operation: Callable[[ExecutionTrace], Awaitable[dict[str, object]]],
) -> StreamingResponse:
    return StreamingResponse(
        _stream_trace(operation),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


def create_app(
    *,
    settings: Settings | None = None,
    engine: Engine | None = None,
    agent_service: AgentService | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    app_engine = engine or create_database_engine(app_settings.database_url)
    create_schema(app_engine)
    manage_agent_service = agent_service is None
    app_agent_service = agent_service or AgentService(app_settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if manage_agent_service:
            await app_agent_service.start()
        try:
            yield
        finally:
            if manage_agent_service:
                await app_agent_service.close()

    app = FastAPI(
        title="Supply Chain AI Control Tower",
        version="0.5.0",
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.state.engine = app_engine
    app.state.agent_service = app_agent_service
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.exception_handler(AccessDeniedError)
    async def access_denied_handler(_request, exc: AccessDeniedError):
        return _json_error(403, str(exc))

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    async def health() -> dict:
        with session_scope(app_engine) as session:
            total_chunks = session.scalar(select(func.count(DocumentChunk.id))) or 0
            if app_engine.dialect.name == "sqlite":
                indexed_chunks = sum(
                    vector is not None
                    for vector in session.scalars(select(DocumentChunk.embedding)).all()
                )
            else:
                indexed_chunks = (
                    session.scalar(
                        select(func.count(DocumentChunk.id)).where(
                            DocumentChunk.embedding.is_not(None)
                        )
                    )
                    or 0
                )
        risk_mcp_status = getattr(
            app_agent_service,
            "mcp_status",
            {
                "enabled": False,
                "state": "not_managed",
                "url": "",
                "tools": [],
                "detail": "Injected agent service does not manage MCP lifecycle.",
            },
        )
        return {
            "status": "ok",
            "database": app_engine.dialect.name,
            "openai_configured": app_settings.openai_configured,
            "document_chunks": total_chunks,
            "indexed_chunks": indexed_chunks,
            "supervisor_model": app_settings.supervisor_model,
            "specialist_model": app_settings.specialist_model,
            "external_risk_mcp": risk_mcp_status,
        }

    @app.get("/api/personas")
    async def personas() -> list[dict]:
        with session_scope(app_engine) as session:
            rows = session.execute(
                select(User.email, User.display_name, Membership.role, Organization.slug)
                .join(Membership, Membership.user_id == User.id)
                .join(Organization, Organization.id == Membership.organization_id)
                .order_by(User.display_name)
            ).all()
        return [
            {
                "email": row[0],
                "display_name": row[1],
                "role": row[2],
                "organization": row[3],
            }
            for row in rows
        ]

    @app.get("/api/conversations")
    async def conversations(user_email: str = Query(...)) -> list[dict]:
        with session_scope(app_engine) as session:
            access = AccessService(session).resolve(user_email, "meridian-assembly")
            records = ConversationService(session).list_for_user(access)
            return [
                {
                    "id": str(record.id),
                    "title": record.title,
                    "created_at": record.created_at,
                    "updated_at": record.updated_at,
                }
                for record in records
            ]

    @app.get("/api/conversations/{conversation_id}/messages")
    async def conversation_messages(
        conversation_id: uuid.UUID,
        user_email: str = Query(...),
    ) -> list[dict]:
        with session_scope(app_engine) as session:
            access = AccessService(session).resolve(user_email, "meridian-assembly")
            messages = ConversationService(session).history(
                access,
                conversation_id,
                limit=50,
            )
            return [asdict(message) for message in messages]

    async def execute_chat(
        request: ChatRequest,
        trace: ExecutionTrace,
    ) -> dict[str, object]:
        trace.start(
            event_type="request",
            node="request",
            label="Operations question received",
            source="application",
            details={"question": request.question, "as_of": request.as_of},
        )
        with session_scope(app_engine) as session:
            trace.start(
                event_type="access",
                node="access",
                label="Resolving deterministic access scope",
                parent_node="request",
                source="application",
            )
            try:
                access = AccessService(session).resolve(
                    request.user_email,
                    "meridian-assembly",
                )
            except Exception:
                trace.fail(node="access", label="Access scope resolution failed")
                raise
            trace.complete(
                node="access",
                label="Access scope resolved",
                details={
                    "organization": access.organization_slug,
                    "role": access.role.value,
                    "warehouse_count": len(access.allowed_warehouse_ids),
                    "supplier_count": len(access.allowed_supplier_ids),
                },
            )
            conversations_service = ConversationService(session)
            if request.conversation_id is None:
                conversation = conversations_service.create(
                    access,
                    title=request.question.strip()[:80],
                )
            else:
                conversation = conversations_service.require(access, request.conversation_id)
            history = conversations_service.history(access, conversation.id)

            try:
                response = await app_agent_service.ask(
                    session,
                    access,
                    question=request.question,
                    as_of=request.as_of,
                    history=history,
                    trace=trace,
                )
            except MissingOpenAIConfiguration as exc:
                trace.fail_open_operations("LLM configuration is missing")
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except Exception:
                trace.fail_open_operations("Agent run failed")
                raise

            conversations_service.append(
                access,
                conversation.id,
                role="user",
                content=request.question,
            )
            assistant_message = conversations_service.append(
                access,
                conversation.id,
                role="assistant",
                content=response.output.answer,
                metadata=response.to_dict(),
            )
            trace.complete(
                node="request",
                label="Request completed",
                details={"conversation_created": request.conversation_id is None},
            )
            payload: dict[str, object] = {
                "conversation_id": str(conversation.id),
                **response.to_dict(),
                "execution_trace": trace.to_list(),
            }
            assistant_message.message_metadata = payload
            return payload

    async def execute_demo(
        request: DemoRequest,
        trace: ExecutionTrace,
    ) -> dict[str, object]:
        trace.start(
            event_type="request",
            node="request",
            label="Offline scenario received",
            source="application",
            details={"question": request.question, "as_of": request.as_of},
        )
        with session_scope(app_engine) as session:
            trace.start(
                event_type="access",
                node="access",
                label="Resolving deterministic access scope",
                parent_node="request",
                source="application",
            )
            try:
                access = AccessService(session).resolve(
                    request.user_email,
                    "meridian-assembly",
                )
            except Exception:
                trace.fail(node="access", label="Access scope resolution failed")
                raise
            trace.complete(
                node="access",
                label="Access scope resolved",
                details={
                    "organization": access.organization_slug,
                    "role": access.role.value,
                    "warehouse_count": len(access.allowed_warehouse_ids),
                    "supplier_count": len(access.allowed_supplier_ids),
                },
            )
            supervisor = SupplyRiskSupervisor(
                inventory=InventorySpecialist(InventoryTools(session)),
                shipments=ShipmentSpecialist(ShipmentTools(session)),
                documents=DocumentSpecialist(DocumentTools(session)),
            )
            try:
                report = supervisor.analyze(
                    request.question,
                    access,
                    as_of=request.as_of,
                    trace=trace,
                )
            except Exception:
                trace.fail_open_operations("Offline analysis failed")
                raise
            trace.complete(
                node="request",
                label="Offline scenario completed",
            )
            return {
                "mode": "deterministic_demo",
                "output": {
                    "answer": report.answer,
                    "key_findings": [finding.summary for finding in report.findings],
                    "citations": _trace_citations(trace),
                    "specialists_used": [finding.specialist for finding in report.findings],
                    "caveats": ["This route uses the fixed local demonstration workflow."],
                },
                "tool_events": [asdict(finding) for finding in report.findings],
                "integrations": {"external_risk_mcp": app_agent_service.mcp_status},
                "execution_trace": trace.to_list(),
            }

    @app.post("/api/chat")
    async def chat(request: ChatRequest) -> dict[str, object]:
        return await execute_chat(request, ExecutionTrace())

    @app.post("/api/chat/stream")
    async def chat_stream(request: ChatRequest) -> StreamingResponse:
        return _streaming_response(lambda trace: execute_chat(request, trace))

    @app.post("/api/demo")
    async def deterministic_demo(request: DemoRequest) -> dict[str, object]:
        return await execute_demo(request, ExecutionTrace())

    @app.post("/api/demo/stream")
    async def deterministic_demo_stream(request: DemoRequest) -> StreamingResponse:
        return _streaming_response(lambda trace: execute_demo(request, trace))

    return app


def _json_error(status_code: int, detail: str):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status_code, content={"detail": detail})


def main() -> None:
    parser = argparse.ArgumentParser(prog="control-tower-web")
    parser.add_argument("--database-url")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    settings = get_settings()
    engine = create_database_engine(args.database_url or settings.database_url)
    uvicorn.run(
        create_app(settings=settings, engine=engine),
        host=args.host or settings.host,
        port=args.port or settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
