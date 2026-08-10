from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from supplyscope.models import DocumentChunk


class EmbeddingProvider(Protocol):
    dimensions: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one embedding per input text in the same order."""


class OpenAIEmbeddingProvider:
    def __init__(self, *, model: str, dimensions: int, client: OpenAI | None = None) -> None:
        self.model = model
        self.dimensions = dimensions
        self.client = client or OpenAI()

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self.client.embeddings.create(
            model=self.model,
            input=list(texts),
            dimensions=self.dimensions,
            encoding_format="float",
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        return [item.embedding for item in ordered]


class EmbeddingIndexer:
    def __init__(self, session: Session, provider: EmbeddingProvider) -> None:
        self.session = session
        self.provider = provider

    def index(self, *, batch_size: int = 64, force: bool = False) -> int:
        if not 1 <= batch_size <= 256:
            raise ValueError("batch_size must be between 1 and 256")

        statement = select(DocumentChunk).order_by(
            DocumentChunk.document_id, DocumentChunk.chunk_index
        )
        chunks = list(self.session.scalars(statement).all())
        if not force:
            chunks = [chunk for chunk in chunks if chunk.embedding is None]

        indexed = 0
        for offset in range(0, len(chunks), batch_size):
            batch = chunks[offset : offset + batch_size]
            vectors = self.provider.embed([chunk.content for chunk in batch])
            if len(vectors) != len(batch):
                raise RuntimeError("Embedding provider returned an unexpected number of vectors.")
            for chunk, vector in zip(batch, vectors, strict=True):
                if len(vector) != self.provider.dimensions:
                    raise RuntimeError("Embedding vector has the wrong dimensions.")
                chunk.embedding = vector
                indexed += 1
            self.session.flush()
        return indexed
