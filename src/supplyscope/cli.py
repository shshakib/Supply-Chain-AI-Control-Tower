from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

from sqlalchemy import select

from supplyscope.access import AccessService
from supplyscope.agent_service import AgentService, MissingOpenAIConfiguration
from supplyscope.agents.specialists import (
    DocumentSpecialist,
    InventorySpecialist,
    ShipmentSpecialist,
)
from supplyscope.agents.supervisor import SupplyRiskSupervisor
from supplyscope.config import get_settings
from supplyscope.database import create_database_engine, create_schema, session_scope
from supplyscope.embeddings import EmbeddingIndexer, OpenAIEmbeddingProvider
from supplyscope.evaluation import DEFAULT_CASES_PATH, run_evaluations
from supplyscope.models import Membership, Organization, User
from supplyscope.synthetic import DEMO_AS_OF, SyntheticDataGenerator
from supplyscope.tools import DocumentTools, InventoryTools, ShipmentTools

DEFAULT_QUESTION = (
    "Which delayed shipments could stop production, and do the responsible supplier "
    "contracts include late-delivery remedies?"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="supplyscope")
    parser.add_argument(
        "--database-url",
        help="Override DATABASE_URL, which is useful for local SQLite smoke tests.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Create the PostgreSQL extension and schema.")

    seed_parser = subparsers.add_parser("seed", help="Generate deterministic demo data.")
    seed_parser.add_argument("--seed", type=int, default=42)
    seed_parser.add_argument("--as-of", type=date.fromisoformat, default=DEMO_AS_OF)

    subparsers.add_parser("personas", help="List available synthetic users.")

    index_parser = subparsers.add_parser(
        "index-documents",
        help="Create OpenAI embeddings for document chunks.",
    )
    index_parser.add_argument("--batch-size", type=int, default=64)
    index_parser.add_argument("--force", action="store_true")

    demo_parser = subparsers.add_parser("demo", help="Run the multi-specialist risk workflow.")
    demo_parser.add_argument("--user", default="noah.east@supplyscope.demo")
    demo_parser.add_argument("--as-of", type=date.fromisoformat, default=DEMO_AS_OF)
    demo_parser.add_argument("--question", default=DEFAULT_QUESTION)
    demo_parser.add_argument("--trace", action="store_true")

    ask_parser = subparsers.add_parser("ask", help="Ask the LLM supervisor a question.")
    ask_parser.add_argument("question")
    ask_parser.add_argument("--user", default="noah.east@supplyscope.demo")
    ask_parser.add_argument("--as-of", type=date.fromisoformat, default=DEMO_AS_OF)
    ask_parser.add_argument("--trace", action="store_true")
    ask_parser.add_argument("--json-output", action="store_true")

    eval_parser = subparsers.add_parser(
        "evaluate",
        help="Run the golden LLM routing and answer-quality cases.",
    )
    eval_parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    eval_parser.add_argument("--limit", type=int)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = get_settings()
    engine = create_database_engine(args.database_url or settings.database_url)

    if args.command == "init-db":
        create_schema(engine)
        print("SupplyScope database schema is ready.")
        return

    if args.command == "seed":
        create_schema(engine)
        with session_scope(engine) as session:
            summary = SyntheticDataGenerator(
                session,
                seed=args.seed,
                as_of=args.as_of,
                document_dir=settings.document_dir,
            ).generate()
            print(json.dumps(asdict(summary), indent=2, default=str))
        return

    if args.command == "index-documents":
        if not settings.openai_configured:
            raise SystemExit("OPENAI_API_KEY is required to index documents.")
        create_schema(engine)
        with session_scope(engine) as session:
            provider = OpenAIEmbeddingProvider(
                model=settings.embedding_model,
                dimensions=settings.embedding_dimensions,
            )
            indexed = EmbeddingIndexer(session, provider).index(
                batch_size=args.batch_size,
                force=args.force,
            )
            print(f"Indexed {indexed} document chunks.")
        return

    if args.command == "evaluate":
        if not settings.openai_configured:
            raise SystemExit("OPENAI_API_KEY is required to run LLM evaluations.")
        report = asyncio.run(
            run_evaluations(
                settings,
                engine,
                cases_path=args.cases,
                limit=args.limit,
            )
        )
        print(json.dumps(report, indent=2, default=str))
        if report["summary"]["failed"]:
            raise SystemExit(1)
        return

    with session_scope(engine) as session:
        if args.command == "personas":
            rows = session.execute(
                select(User.email, User.display_name, Membership.role, Organization.slug)
                .join(Membership, Membership.user_id == User.id)
                .join(Organization, Organization.id == Membership.organization_id)
                .order_by(User.email)
            ).all()
            for email, display_name, role, organization_slug in rows:
                print(f"{email:40} {role:24} {display_name} ({organization_slug})")
            return

        access = AccessService(session).resolve(args.user, "meridian-assembly")
        if args.command == "ask":
            try:
                response = asyncio.run(
                    AgentService(settings).ask(
                        session,
                        access,
                        question=args.question,
                        as_of=args.as_of,
                    )
                )
            except MissingOpenAIConfiguration as exc:
                raise SystemExit(str(exc)) from exc
            if args.json_output:
                print(json.dumps(response.to_dict(), indent=2, default=str))
            else:
                print(response.output.answer)
                if response.output.citations:
                    print("\nSources:")
                    for citation in response.output.citations:
                        print(f"- {citation}")
                if args.trace:
                    print("\nTool trace:\n")
                    print(json.dumps(response.to_dict()["tool_events"], indent=2, default=str))
            return

        supervisor = SupplyRiskSupervisor(
            inventory=InventorySpecialist(InventoryTools(session)),
            shipments=ShipmentSpecialist(ShipmentTools(session)),
            documents=DocumentSpecialist(DocumentTools(session)),
        )
        report = supervisor.analyze(args.question, access, as_of=args.as_of)
        print(report.answer)
        if args.trace:
            print("\nSpecialist trace:\n")
            print(json.dumps(report.to_dict()["findings"], indent=2, default=str))


if __name__ == "__main__":
    main()
