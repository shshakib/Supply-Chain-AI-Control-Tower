from __future__ import annotations

import asyncio
from collections.abc import Sequence
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from control_tower.access import AccessService
from control_tower.analytics import ScopeResolver
from control_tower.embeddings import EmbeddingIndexer, validate_embedding_config
from control_tower.models import DocumentChunk
from control_tower.retrieval import HybridDocumentRetriever


class KeywordEmbeddingProvider:
    model = "test-keyword-v1"
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


def test_embedding_failure_falls_back_without_leaking_scope(session: Session, monkeypatch) -> None:
    provider = KeywordEmbeddingProvider()
    EmbeddingIndexer(session, provider).index()
    access = AccessService(session).resolve("mia.west@controltower.demo", "meridian-assembly")

    def fail(_texts):
        raise TimeoutError("private provider response must not appear in output")

    monkeypatch.setattr(provider, "embed", fail)
    retriever = HybridDocumentRetriever(session, provider)
    results = retriever.search(access, query="delivery unloading", limit=20)
    assert results
    assert all(item.retrieval_method == "keyword" for item in results)
    assert all(item.document_type != "incident_report" for item in results)
    assert "keyword" in retriever.last_warning
    assert "private" not in retriever.last_warning
    assert retriever.search(access, query="nonexistentword") == []
    assert retriever.last_warning


@pytest.mark.parametrize("vectors", [[], [[1.0]], [[float("nan")] * 6], [[0.0] * 6]])
def test_invalid_query_vectors_fall_back(session: Session, monkeypatch, vectors) -> None:
    provider = KeywordEmbeddingProvider()
    EmbeddingIndexer(session, provider).index()
    monkeypatch.setattr(provider, "embed", lambda _texts: vectors)
    access = AccessService(session).resolve("noah.east@controltower.demo", "meridian-assembly")
    retriever = HybridDocumentRetriever(session, provider)
    results = retriever.search(access, query="delivery credit")
    assert results
    assert all(item.retrieval_method == "keyword" for item in results)
    assert retriever.last_warning


def test_changed_model_and_legacy_vectors_require_reindex(session: Session) -> None:
    provider = KeywordEmbeddingProvider()
    count = EmbeddingIndexer(session, provider).index()
    assert EmbeddingIndexer(session, provider).index() == 0
    provider.model = "test-keyword-v2"
    access = AccessService(session).resolve("noah.east@controltower.demo", "meridian-assembly")
    retriever = HybridDocumentRetriever(session, provider)
    assert all(
        item.retrieval_method == "keyword" for item in retriever.search(access, query="credit")
    )
    assert "index-documents" in retriever.last_warning
    assert EmbeddingIndexer(session, provider).index() == count
    assert all(
        item.retrieval_method == "hybrid" for item in retriever.search(access, query="credit")
    )
    assert retriever.last_warning is None
    chunk = session.scalars(select(DocumentChunk)).first()
    chunk.embedding_model = None
    session.flush()
    assert EmbeddingIndexer(session, provider).index() == 1
    assert chunk.embedding_model == provider.model


def test_wrong_dimensions_rejected_before_postgresql_embedding_calls() -> None:
    session = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    )
    with pytest.raises(ValueError, match="384"):
        validate_embedding_config(session, KeywordEmbeddingProvider())


def test_invalid_index_batch_does_not_write_partial_vectors(session: Session, monkeypatch) -> None:
    provider = KeywordEmbeddingProvider()
    monkeypatch.setattr(provider, "embed", lambda texts: [[1.0] * 6] * (len(texts) - 1) + [[1.0]])
    with pytest.raises(ValueError, match="dimensions"):
        EmbeddingIndexer(session, provider).index()
    assert all(chunk.embedding is None for chunk in session.scalars(select(DocumentChunk)))


def test_dimension_change_rebuilds_sqlite_vectors(session: Session) -> None:
    provider = KeywordEmbeddingProvider()
    count = EmbeddingIndexer(session, provider).index()

    class WiderProvider(KeywordEmbeddingProvider):
        dimensions = 8

        def embed(self, texts):
            return [vector + [0.0, 0.0] for vector in super().embed(texts)]

    wider = WiderProvider()
    retriever = HybridDocumentRetriever(session, wider)
    access = AccessService(session).resolve("noah.east@controltower.demo", "meridian-assembly")
    assert retriever.search(access, query="credit")[0].retrieval_method == "keyword"
    assert EmbeddingIndexer(session, wider).index() == count
    assert retriever.search(access, query="credit")[0].retrieval_method == "hybrid"
    assert all(chunk.embedding_dimensions == 8 for chunk in session.scalars(select(DocumentChunk)))


def test_database_errors_are_not_silently_treated_as_embedding_failure(
    session: Session, monkeypatch
) -> None:
    access = AccessService(session).resolve("noah.east@controltower.demo", "meridian-assembly")
    retriever = HybridDocumentRetriever(session, KeywordEmbeddingProvider())

    def fail(*_args, **_kwargs):
        raise OperationalError("test", {}, RuntimeError("database unavailable"))

    monkeypatch.setattr(session, "execute", fail)
    with pytest.raises(OperationalError):
        retriever.search(access, query="credit")


def test_async_retrieval_cancels_embedding_without_swallowing_cancel(session: Session) -> None:
    cancelled = []

    class SlowProvider(KeywordEmbeddingProvider):
        async def aembed(self, texts):
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.append(True)

    provider = SlowProvider()
    EmbeddingIndexer(session, provider).index()
    access = AccessService(session).resolve("noah.east@controltower.demo", "meridian-assembly")
    retriever = HybridDocumentRetriever(session, provider)

    async def search():
        async with asyncio.timeout(0.05):
            return await retriever.asearch(access, query="credit")

    with pytest.raises(TimeoutError):
        asyncio.run(search())
    assert cancelled == [True]


def test_async_embedding_failure_preserves_keyword_fallback(session: Session) -> None:
    class FailedProvider(KeywordEmbeddingProvider):
        async def aembed(self, _texts):
            raise TimeoutError("private provider detail")

    provider = FailedProvider()
    EmbeddingIndexer(session, provider).index()
    access = AccessService(session).resolve("noah.east@controltower.demo", "meridian-assembly")
    retriever = HybridDocumentRetriever(session, provider)
    results = asyncio.run(retriever.asearch(access, query="credit"))
    assert results and all(item.retrieval_method == "keyword" for item in results)
    assert "private" not in retriever.last_warning
