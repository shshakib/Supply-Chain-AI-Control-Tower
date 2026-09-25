from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol

from openai import AsyncOpenAI, OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from control_tower.models import EMBEDDING_DIMENSIONS, DocumentChunk


class EmbeddingProvider(Protocol):
    model: str
    dimensions: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one embedding per input text in the same order."""

    async def aembed(self, texts: Sequence[str]) -> list[list[float]]:
        """Cancellable embedding request for the web workflow."""


class OpenAIEmbeddingProvider:
    def __init__(self, *, model: str, dimensions: int, client: OpenAI | None = None) -> None:
        self.model = model
        self.dimensions = dimensions
        self.client = client or OpenAI(timeout=10.0, max_retries=0)

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

    async def aembed(self, texts: Sequence[str]) -> list[list[float]]:
        async with AsyncOpenAI(timeout=10.0, max_retries=0) as client:
            response = await client.embeddings.create(
                model=self.model,
                input=list(texts),
                dimensions=self.dimensions,
                encoding_format="float",
            )
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]


def validate_embedding_config(session: Session, provider: EmbeddingProvider) -> None:
    if not provider.model.strip() or len(provider.model) > 255 or provider.dimensions < 1:
        raise ValueError("Embedding model and positive dimensions are required.")
    if (
        session.get_bind().dialect.name == "postgresql"
        and provider.dimensions != EMBEDDING_DIMENSIONS
    ):
        raise ValueError(f"PostgreSQL requires {EMBEDDING_DIMENSIONS}-dimension embeddings.")


def validate_vectors(vectors: list[list[float]], *, count: int, dimensions: int) -> None:
    if len(vectors) != count:
        raise ValueError("Embedding provider returned an unexpected number of vectors.")
    for vector in vectors:
        if len(vector) != dimensions or not all(math.isfinite(value) for value in vector):
            raise ValueError("Embedding vector has invalid dimensions or non-finite values.")


class EmbeddingIndexer:
    def __init__(self, session: Session, provider: EmbeddingProvider) -> None:
        self.session = session
        self.provider = provider

    def index(self, *, batch_size: int = 64, force: bool = False) -> int:
        if not 1 <= batch_size <= 256:
            raise ValueError("batch_size must be between 1 and 256")
        validate_embedding_config(self.session, self.provider)

        statement = select(DocumentChunk).order_by(
            DocumentChunk.document_id, DocumentChunk.chunk_index
        )
        chunks = list(self.session.scalars(statement).all())
        if not force:
            chunks = [
                chunk
                for chunk in chunks
                if chunk.embedding is None
                or chunk.embedding_model != self.provider.model
                or chunk.embedding_dimensions != self.provider.dimensions
            ]

        indexed = 0
        for offset in range(0, len(chunks), batch_size):
            batch = chunks[offset : offset + batch_size]
            vectors = self.provider.embed([chunk.content for chunk in batch])
            validate_vectors(vectors, count=len(batch), dimensions=self.provider.dimensions)
            for chunk, vector in zip(batch, vectors, strict=True):
                chunk.embedding = vector
                chunk.embedding_model = self.provider.model
                chunk.embedding_dimensions = self.provider.dimensions
                indexed += 1
            self.session.flush()
        return indexed
