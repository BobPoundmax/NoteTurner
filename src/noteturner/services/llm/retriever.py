import logging
from dataclasses import dataclass
from typing import Protocol

from noteturner.db.repositories.vectors import search_chunks
from noteturner.db.session import session_scope
from noteturner.integrations.openrouter import OpenRouterClient, OpenRouterError

logger = logging.getLogger(__name__)


@dataclass
class SourceChunk:
    text: str
    source: str


class ContextRetriever(Protocol):
    async def retrieve(self, query: str, *, include_financial: bool) -> list[SourceChunk]:
        ...


class NullRetriever:
    """Retriever that returns no context (used when no vector store is configured)."""

    async def retrieve(self, query: str, *, include_financial: bool) -> list[SourceChunk]:
        return []


class VectorRetriever:
    """Retrieves relevant chunks from the pgvector store using OpenRouter embeddings."""

    def __init__(self, openrouter: OpenRouterClient, *, limit: int = 5) -> None:
        self._openrouter = openrouter
        self._limit = limit

    async def retrieve(self, query: str, *, include_financial: bool) -> list[SourceChunk]:
        try:
            embeddings = await self._openrouter.embed([query])
        except OpenRouterError as exc:
            logger.warning("Embedding query failed, returning no context: %s", exc)
            return []
        if not embeddings:
            return []

        try:
            async with session_scope() as session:
                chunks = await search_chunks(
                    session,
                    embedding=embeddings[0],
                    include_financial=include_financial,
                    limit=self._limit,
                )
        except RuntimeError:
            return []

        return [SourceChunk(text=chunk.content, source=chunk.title or chunk.source) for chunk in chunks]
