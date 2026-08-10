from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, replace

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from supplyscope.access import AccessContext
from supplyscope.embeddings import EmbeddingProvider
from supplyscope.models import Document, DocumentChunk


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    title: str
    document_type: str
    heading: str | None
    content: str
    source_filename: str
    chunk_index: int
    citation: str
    score: float
    retrieval_method: str


class HybridDocumentRetriever:
    def __init__(self, session: Session, embedding_provider: EmbeddingProvider | None) -> None:
        self.session = session
        self.embedding_provider = embedding_provider

    def search(
        self,
        access: AccessContext,
        *,
        query: str,
        limit: int = 6,
        supplier_id: uuid.UUID | None = None,
        document_type: str | None = None,
    ) -> list[RetrievedChunk]:
        if not query.strip():
            raise ValueError("query cannot be empty")
        if not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        if supplier_id is not None:
            access.require_supplier(supplier_id)

        candidate_limit = min(limit * 4, 60)
        keyword = self._keyword_search(
            access,
            query=query,
            limit=candidate_limit,
            supplier_id=supplier_id,
            document_type=document_type,
        )
        semantic = self._semantic_search(
            access,
            query=query,
            limit=candidate_limit,
            supplier_id=supplier_id,
            document_type=document_type,
        )
        if not semantic:
            return keyword[:limit]

        by_id = {item.chunk_id: item for item in [*keyword, *semantic]}
        fused_scores: dict[uuid.UUID, float] = {}
        for result_set in (keyword, semantic):
            for rank, item in enumerate(result_set, start=1):
                fused_scores[item.chunk_id] = fused_scores.get(item.chunk_id, 0.0) + 1 / (60 + rank)

        ranked = sorted(fused_scores, key=fused_scores.get, reverse=True)
        return [
            replace(
                by_id[chunk_id],
                score=round(fused_scores[chunk_id], 6),
                retrieval_method="hybrid",
            )
            for chunk_id in ranked[:limit]
        ]

    def _scope_conditions(
        self,
        access: AccessContext,
        *,
        supplier_id: uuid.UUID | None,
        document_type: str | None,
    ) -> list:
        warehouse_scope = (
            or_(
                Document.warehouse_id.is_(None),
                Document.warehouse_id.in_(access.allowed_warehouse_ids),
            )
            if access.allowed_warehouse_ids
            else Document.warehouse_id.is_(None)
        )
        supplier_scope = (
            or_(
                Document.supplier_id.is_(None),
                Document.supplier_id.in_(access.allowed_supplier_ids),
            )
            if access.allowed_supplier_ids
            else Document.supplier_id.is_(None)
        )
        conditions = [
            Document.organization_id == access.organization_id,
            warehouse_scope,
            supplier_scope,
        ]
        if supplier_id is not None:
            conditions.append(Document.supplier_id == supplier_id)
        if document_type is not None:
            conditions.append(Document.document_type == document_type)
        return conditions

    def _base_select(self):
        return select(
            DocumentChunk.id,
            Document.id,
            Document.title,
            Document.document_type,
            DocumentChunk.heading,
            DocumentChunk.content,
            Document.source_filename,
            DocumentChunk.chunk_index,
        ).join(Document, Document.id == DocumentChunk.document_id)

    @staticmethod
    def _to_chunk(row, *, score: float, method: str) -> RetrievedChunk:
        return RetrievedChunk(
            chunk_id=row[0],
            document_id=row[1],
            title=row[2],
            document_type=row[3],
            heading=row[4],
            content=row[5],
            source_filename=row[6],
            chunk_index=row[7],
            citation=f"{row[6]}#chunk-{row[7]}",
            score=round(score, 6),
            retrieval_method=method,
        )

    def _keyword_search(
        self,
        access: AccessContext,
        *,
        query: str,
        limit: int,
        supplier_id: uuid.UUID | None,
        document_type: str | None,
    ) -> list[RetrievedChunk]:
        terms = [term.strip(".,:;!?()[]").lower() for term in query.split()]
        terms = [term for term in terms if len(term) >= 3]
        if not terms:
            return []

        rows = self.session.execute(
            self._base_select()
            .where(
                and_(
                    *self._scope_conditions(
                        access,
                        supplier_id=supplier_id,
                        document_type=document_type,
                    ),
                    or_(*[DocumentChunk.content.ilike(f"%{term}%") for term in terms]),
                )
            )
            .limit(200)
        ).all()

        scored = []
        for row in rows:
            normalized = row[5].lower()
            hits = sum(normalized.count(term) for term in terms)
            score = hits / max(len(terms), 1)
            scored.append(self._to_chunk(row, score=score, method="keyword"))
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:limit]

    def _semantic_search(
        self,
        access: AccessContext,
        *,
        query: str,
        limit: int,
        supplier_id: uuid.UUID | None,
        document_type: str | None,
    ) -> list[RetrievedChunk]:
        if self.embedding_provider is None:
            return []
        query_vector = self.embedding_provider.embed([query])[0]
        conditions = self._scope_conditions(
            access,
            supplier_id=supplier_id,
            document_type=document_type,
        )
        conditions.append(DocumentChunk.embedding.is_not(None))

        if self.session.bind is not None and self.session.bind.dialect.name == "postgresql":
            distance = DocumentChunk.embedding.cosine_distance(query_vector)
            rows = self.session.execute(
                self._base_select()
                .add_columns(distance.label("distance"))
                .where(*conditions, distance <= 0.85)
                .order_by(distance)
                .limit(limit)
            ).all()
            return [
                self._to_chunk(row[:8], score=1.0 - float(row[8]), method="semantic")
                for row in rows
            ]

        rows = self.session.execute(
            self._base_select().add_columns(DocumentChunk.embedding).where(*conditions)
        ).all()
        scored = [
            self._to_chunk(
                row[:8],
                score=self._cosine_similarity(query_vector, row[8]),
                method="semantic",
            )
            for row in rows
        ]
        scored = [item for item in scored if item.score >= 0.15]
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:limit]

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        if len(left) != len(right):
            return 0.0
        dot = sum(a * b for a, b in zip(left, right, strict=True))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return dot / (left_norm * right_norm)
