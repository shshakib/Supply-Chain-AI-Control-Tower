from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy import Engine

from control_tower.access import AccessService
from control_tower.agent_service import AgentService
from control_tower.config import Settings
from control_tower.database import session_scope
from control_tower.synthetic import DEMO_AS_OF

DEFAULT_CASES_PATH = Path(__file__).resolve().parents[2] / "evals" / "cases.json"


class EvaluationCase(BaseModel):
    id: str
    user_email: str
    question: str
    expected_specialists: list[str] = Field(default_factory=list)
    expected_terms: list[str] = Field(default_factory=list)
    expected_evidence_sources: list[str] = Field(default_factory=list)
    forbidden_terms: list[str] = Field(default_factory=list)


def load_cases(path: Path = DEFAULT_CASES_PATH) -> list[EvaluationCase]:
    return [EvaluationCase.model_validate(item) for item in json.loads(path.read_text("utf-8"))]


async def run_evaluations(
    settings: Settings,
    engine: Engine,
    *,
    cases_path: Path = DEFAULT_CASES_PATH,
    limit: int | None = None,
) -> dict:
    cases = load_cases(cases_path)
    if limit is not None:
        cases = cases[:limit]
    service = AgentService(settings)
    results = []

    async with service:
        for case in cases:
            with session_scope(engine) as session:
                access = AccessService(session).resolve(case.user_email, "meridian-assembly")
                response = await service.ask(
                    session,
                    access,
                    question=case.question,
                    as_of=DEMO_AS_OF,
                )

            output = response.output
            searchable = " ".join([output.answer, *output.key_findings, *output.citations]).lower()
            actual_specialists = set(output.specialists_used)
            specialist_pass = set(case.expected_specialists).issubset(actual_specialists)
            terms_pass = all(term.lower() in searchable for term in case.expected_terms)
            forbidden_pass = all(term.lower() not in searchable for term in case.forbidden_terms)
            actual_sources = {event.source for event in response.tool_events}
            evidence_source_pass = set(case.expected_evidence_sources).issubset(actual_sources)
            passed = specialist_pass and terms_pass and forbidden_pass and evidence_source_pass
            results.append(
                {
                    "id": case.id,
                    "passed": passed,
                    "specialist_pass": specialist_pass,
                    "terms_pass": terms_pass,
                    "forbidden_pass": forbidden_pass,
                    "evidence_source_pass": evidence_source_pass,
                    "actual_evidence_sources": sorted(actual_sources),
                    "actual_specialists": output.specialists_used,
                    "citations": output.citations,
                    "answer": output.answer,
                }
            )

    passed_count = sum(item["passed"] for item in results)
    return {
        "summary": {
            "passed": passed_count,
            "failed": len(results) - passed_count,
            "total": len(results),
            "pass_rate": round(passed_count / len(results), 3) if results else 0.0,
        },
        "results": results,
    }
