from dataclasses import dataclass
from typing import Protocol


@dataclass
class SourceChunk:
    text: str
    source: str


class ContextRetriever(Protocol):
    async def retrieve(self, query: str, *, include_financial: bool) -> list[SourceChunk]:
        ...


class NullRetriever:
    """Retriever that returns no context. Replaced by a vector retriever in Phase 4."""

    async def retrieve(self, query: str, *, include_financial: bool) -> list[SourceChunk]:
        return []
