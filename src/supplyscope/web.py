from __future__ import annotations

import argparse
import uuid
from dataclasses import asdict
from datetime import date
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import Engine, func, select

from supplyscope.access import AccessDeniedError, AccessService
from supplyscope.agent_service import AgentService, MissingOpenAIConfiguration
from supplyscope.agents.specialists import (
    DocumentSpecialist,
    InventorySpecialist,
    ShipmentSpecialist,
)
from supplyscope.agents.supervisor import SupplyRiskSupervisor
from supplyscope.config import Settings, get_settings
from supplyscope.conversations import ConversationService
from supplyscope.database import create_database_engine, create_schema, session_scope
from supplyscope.models import DocumentChunk, Membership, Organization, User
from supplyscope.synthetic import DEMO_AS_OF
from supplyscope.tools import DocumentTools, InventoryTools, ShipmentTools

STATIC_DIR = Path(__file__).parent / "static"


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    user_email: str
    conversation_id: uuid.UUID | None = None
    as_of: date = DEMO_AS_OF


class DemoRequest(BaseModel):
    user_email: str = "noah.east@supplyscope.demo"
    question: str = (
        "Which delayed shipments could stop production, and do the responsible supplier "
        "contracts include late-delivery remedies?"
    )
    as_of: date = DEMO_AS_OF


def create_app(
    *,
    settings: Settings | None = None,
    engine: Engine | None = None,
    agent_service: AgentService | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    app_engine = engine or create_database_engine(app_settings.database_url)
    create_schema(app_engine)
    app_agent_service = agent_service or AgentService(app_settings)

    app = FastAPI(title="SupplyScope", version="0.2.0")
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
        return {
            "status": "ok",
            "database": app_engine.dialect.name,
            "openai_configured": app_settings.openai_configured,
            "document_chunks": total_chunks,
            "indexed_chunks": indexed_chunks,
            "supervisor_model": app_settings.supervisor_model,
            "specialist_model": app_settings.specialist_model,
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

    @app.post("/api/chat")
    async def chat(request: ChatRequest) -> dict:
        with session_scope(app_engine) as session:
            access = AccessService(session).resolve(request.user_email, "meridian-assembly")
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
                )
            except MissingOpenAIConfiguration as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc

            conversations_service.append(
                access,
                conversation.id,
                role="user",
                content=request.question,
            )
            conversations_service.append(
                access,
                conversation.id,
                role="assistant",
                content=response.output.answer,
                metadata=response.to_dict(),
            )
            return {
                "conversation_id": str(conversation.id),
                **response.to_dict(),
            }

    @app.post("/api/demo")
    async def deterministic_demo(request: DemoRequest) -> dict:
        with session_scope(app_engine) as session:
            access = AccessService(session).resolve(request.user_email, "meridian-assembly")
            supervisor = SupplyRiskSupervisor(
                inventory=InventorySpecialist(InventoryTools(session)),
                shipments=ShipmentSpecialist(ShipmentTools(session)),
                documents=DocumentSpecialist(DocumentTools(session)),
            )
            report = supervisor.analyze(request.question, access, as_of=request.as_of)
            return {
                "mode": "deterministic_demo",
                "output": {
                    "answer": report.answer,
                    "key_findings": [finding.summary for finding in report.findings],
                    "citations": [],
                    "specialists_used": [finding.specialist for finding in report.findings],
                    "caveats": ["This route uses the fixed local demonstration workflow."],
                },
                "tool_events": [asdict(finding) for finding in report.findings],
            }

    return app


def _json_error(status_code: int, detail: str):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status_code, content={"detail": detail})


def main() -> None:
    parser = argparse.ArgumentParser(prog="supplyscope-web")
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
