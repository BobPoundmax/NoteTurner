import logging
from dataclasses import dataclass, field
from typing import Protocol

from noteturner.db.repositories.vectors import ChunkMatch, search_chunk_matches
from noteturner.db.session import session_scope
from noteturner.integrations.openrouter import OpenRouterClient, OpenRouterError

logger = logging.getLogger(__name__)
MAX_VECTOR_DISTANCE = 0.72


@dataclass
class SourceChunk:
    text: str
    source: str
    source_type: str | None = None
    record_type: str | None = None
    distance: float | None = None


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
    "завтра",
    "сегодня",
)
MARKETING_KEYWORDS = ("лид", "заявк", "utm", "источник", "обращен")
CRM_KEYWORDS = ("crm", "ученик", "клиент", "компан", "hollihop")


def classify_query_preferences(question: str) -> QueryPreferences:
    text = (question or "").lower()
    if any(keyword in text for keyword in FINANCE_KEYWORDS):
        return QueryPreferences(
            preferred_sources=["hollihop"],
            preferred_record_types=["balance", "payment", "group_payer", "group_fiscal"],
            requires_corporate_context=True,
        )
    if any(keyword in text for keyword in GROUP_KEYWORDS):
        return QueryPreferences(
            preferred_sources=["hollihop"],
            preferred_record_types=[
                "schedule_item",
                "schedule_day",
                "edunit",
                "edunit_student",
                "student",
            ],
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
                "schedule_item",
                "schedule_day",
                "group_payer",
                "group_fiscal",
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
    def _dedupe_matches(matches: list[ChunkMatch]) -> list[ChunkMatch]:
        best_by_key: dict[tuple[str, str, str], ChunkMatch] = {}
        for match in matches:
            chunk = match.chunk
            key = (chunk.source, chunk.external_id, chunk.content)
            existing = best_by_key.get(key)
            if existing is None or match.distance < existing.distance:
                best_by_key[key] = match
        return list(best_by_key.values())

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
                preferred_matches: list[ChunkMatch] = []
                broad_matches: list[ChunkMatch] = []
                if preferences.preferred_sources or preferences.preferred_record_types:
                    preferred_matches = await search_chunk_matches(
                        session,
                        embedding=embeddings[0],
                        include_financial=include_financial,
                        sources=preferences.preferred_sources or None,
                        record_types=preferences.preferred_record_types or None,
                        limit=max(self._limit, self._limit * 2),
                        max_distance=MAX_VECTOR_DISTANCE,
                    )
                if not preferred_matches or len(preferred_matches) < self._limit:
                    broad_matches = await search_chunk_matches(
                        session,
                        embedding=embeddings[0],
                        include_financial=include_financial,
                        limit=max(self._limit, self._limit * 2),
                        max_distance=MAX_VECTOR_DISTANCE,
                    )
                matches = self._dedupe_matches([*preferred_matches, *broad_matches])
                matches.sort(
                    key=lambda match: (
                        0
                        if not preferences.preferred_record_types
                        or match.chunk.record_type in preferences.preferred_record_types
                        else 1,
                        match.distance,
                    )
                )
                matches = matches[: self._limit]
        except RuntimeError:
            return []

        if matches:
            logger.info(
                "Retriever query=%r preferred_types=%s matches=%s",
                query[:120],
                preferences.preferred_record_types,
                [f"{match.chunk.record_type}:{match.distance:.3f}" for match in matches],
            )

        return [
            SourceChunk(
                text=match.chunk.content,
                source=match.chunk.title or match.chunk.source,
                source_type=match.chunk.source,
                record_type=match.chunk.record_type,
                distance=match.distance,
            )
            for match in matches
        ]
