from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy import Engine

from supplyscope.access import AccessService
from supplyscope.agent_service import AgentService
from supplyscope.config import Settings
from supplyscope.database import session_scope
from supplyscope.synthetic import DEMO_AS_OF

DEFAULT_CASES_PATH = Path(__file__).resolve().parents[2] / "evals" / "cases.json"


class EvaluationCase(BaseModel):
    id: str
    user_email: str
    question: str
    expected_specialists: list[str] = Field(default_factory=list)
    expected_terms: list[str] = Field(default_factory=list)
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
        passed = specialist_pass and terms_pass and forbidden_pass
        results.append(
            {
                "id": case.id,
                "passed": passed,
                "specialist_pass": specialist_pass,
                "terms_pass": terms_pass,
                "forbidden_pass": forbidden_pass,
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
