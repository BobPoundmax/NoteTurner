import logging
from dataclasses import dataclass, field
from typing import Protocol

from noteturner.db.repositories.vectors import search_chunks
from noteturner.db.session import session_scope
from noteturner.integrations.openrouter import OpenRouterClient, OpenRouterError

logger = logging.getLogger(__name__)


@dataclass
class SourceChunk:
    text: str
    source: str
    source_type: str | None = None
    record_type: str | None = None


@dataclass
class QueryPreferences:
    preferred_sources: list[str] = field(default_factory=list)
    preferred_record_types: list[str] = field(default_factory=list)
    requires_corporate_context: bool = False


FINANCE_KEYWORDS = (
    "платеж",
    "оплат",
    "баланс",
    "долг",
    "задолж",
    "выручк",
    "деньг",
    "счет",
    "доход",
    "списан",
)
GROUP_KEYWORDS = (
    "групп",
    "расписан",
    "заняти",
    "урок",
    "учебн",
    "преподав",
    "договор",
    "плательщ",
)
MARKETING_KEYWORDS = ("лид", "заявк", "utm", "источник", "обращен")
CRM_KEYWORDS = ("crm", "ученик", "клиент", "компан", "hollihop")


def classify_query_preferences(question: str) -> QueryPreferences:
    text = (question or "").lower()
    if any(keyword in text for keyword in FINANCE_KEYWORDS):
        return QueryPreferences(
            preferred_sources=["hollihop"],
            preferred_record_types=["balance", "payment", "edunit_student", "edunit"],
            requires_corporate_context=True,
        )
    if any(keyword in text for keyword in GROUP_KEYWORDS):
        return QueryPreferences(
            preferred_sources=["hollihop"],
            preferred_record_types=["edunit", "edunit_student", "student"],
            requires_corporate_context=True,
        )
    if any(keyword in text for keyword in MARKETING_KEYWORDS):
        return QueryPreferences(
            preferred_sources=["hollihop"],
            preferred_record_types=["study_request", "lead", "student"],
            requires_corporate_context=True,
        )
    if any(keyword in text for keyword in CRM_KEYWORDS):
        return QueryPreferences(
            preferred_sources=["hollihop"],
            preferred_record_types=[
                "student",
                "lead",
                "payment",
                "balance",
                "study_request",
                "edunit",
                "edunit_student",
            ],
            requires_corporate_context=True,
        )
    return QueryPreferences()


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

    @staticmethod
    def _dedupe(chunks: list) -> list:
        seen: set[tuple[str, str, str]] = set()
        unique = []
        for chunk in chunks:
            key = (chunk.source, chunk.external_id, chunk.content)
            if key in seen:
                continue
            seen.add(key)
            unique.append(chunk)
        return unique

    async def retrieve(self, query: str, *, include_financial: bool) -> list[SourceChunk]:
        preferences = classify_query_preferences(query)
        try:
            embeddings = await self._openrouter.embed([query])
        except OpenRouterError as exc:
            logger.warning("Embedding query failed, returning no context: %s", exc)
            return []
        if not embeddings:
            return []

        try:
            async with session_scope() as session:
                chunks = []
                if preferences.preferred_sources or preferences.preferred_record_types:
                    chunks.extend(
                        await search_chunks(
                            session,
                            embedding=embeddings[0],
                            include_financial=include_financial,
                            sources=preferences.preferred_sources or None,
                            record_types=preferences.preferred_record_types or None,
                            limit=max(2, min(3, self._limit)),
                        )
                    )
                chunks.extend(
                    await search_chunks(
                        session,
                        embedding=embeddings[0],
                        include_financial=include_financial,
                        limit=max(self._limit, self._limit * 2),
                    )
                )
                chunks = self._dedupe(chunks)[: self._limit]
        except RuntimeError:
            return []

        return [
            SourceChunk(
                text=chunk.content,
                source=chunk.title or chunk.source,
                source_type=chunk.source,
                record_type=chunk.record_type,
            )
            for chunk in chunks
        ]
