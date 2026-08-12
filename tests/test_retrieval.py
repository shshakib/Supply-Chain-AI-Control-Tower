from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from control_tower.access import AccessService
from control_tower.analytics import ScopeResolver
from control_tower.embeddings import EmbeddingIndexer
from control_tower.retrieval import HybridDocumentRetriever


class KeywordEmbeddingProvider:
    dimensions = 6
    vocabulary = ("late", "delivery", "credit", "unloading", "inventory", "quality")

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float(text.lower().count(term)) for term in self.vocabulary] for text in texts]


def test_hybrid_retrieval_returns_scoped_contract_citation(session: Session) -> None:
    provider = KeywordEmbeddingProvider()
    indexed = EmbeddingIndexer(session, provider).index()
    access = AccessService(session).resolve(
        "noah.east@controltower.demo",
        "meridian-assembly",
    )
    supplier_id = ScopeResolver(session, access).supplier_id("SUP-001")

    results = HybridDocumentRetriever(session, provider).search(
        access,
        query="late delivery credit",
        supplier_id=supplier_id,
        limit=4,
    )

    assert indexed > 0
    assert any("4%" in result.content for result in results)
    assert all("sup-002" not in result.source_filename for result in results)
    assert all(result.retrieval_method == "hybrid" for result in results)
    assert all("#chunk-" in result.citation for result in results)


def test_hybrid_retrieval_does_not_leak_regional_incident(session: Session) -> None:
    provider = KeywordEmbeddingProvider()
    EmbeddingIndexer(session, provider).index(force=True)
    access = AccessService(session).resolve(
        "mia.west@controltower.demo",
        "meridian-assembly",
    )

    results = HybridDocumentRetriever(session, provider).search(
        access,
        query="unloading terminal",
        limit=10,
    )

    assert all(result.document_type != "incident_report" for result in results)
