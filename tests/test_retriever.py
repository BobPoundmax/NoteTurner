from contextlib import asynccontextmanager
from dataclasses import dataclass
from unittest.mock import AsyncMock

import noteturner.services.llm.retriever as retriever_mod
from noteturner.db.repositories.vectors import ChunkMatch
from noteturner.integrations.openrouter import OpenRouterError
from noteturner.services.llm.retriever import VectorRetriever


@dataclass
class _FakeChunk:
    content: str
    title: str | None
    source: str
    external_id: str = "x"
    record_type: str = "doc"


async def test_vector_retriever_returns_source_chunks(monkeypatch) -> None:
    @asynccontextmanager
    async def fake_scope():
        yield object()

    async def fake_search(
        session,
        *,
        embedding,
        include_financial,
        sources=None,
        record_types=None,
        limit,
        max_distance=None,
    ):
        assert embedding == [0.1, 0.2]
        return [ChunkMatch(chunk=_FakeChunk(content="текст", title="Отчёт.pdf", source="gdrive"), distance=0.12)]

    monkeypatch.setattr(retriever_mod, "session_scope", fake_scope)
    monkeypatch.setattr(retriever_mod, "search_chunk_matches", fake_search)

    openrouter = AsyncMock()
    openrouter.embed = AsyncMock(return_value=[[0.1, 0.2]])

    chunks = await VectorRetriever(openrouter).retrieve("вопрос", include_financial=False)

    assert len(chunks) == 1
    assert chunks[0].text == "текст"
    assert chunks[0].source == "Отчёт.pdf"


async def test_vector_retriever_handles_embed_failure() -> None:
    openrouter = AsyncMock()
    openrouter.embed = AsyncMock(side_effect=OpenRouterError("boom"))

    chunks = await VectorRetriever(openrouter).retrieve("вопрос", include_financial=True)

    assert chunks == []


async def test_vector_retriever_prefers_hollihop_finance(monkeypatch) -> None:
    calls = []

    @asynccontextmanager
    async def fake_scope():
        yield object()

    async def fake_search(
        session,
        *,
        embedding,
        include_financial,
        sources=None,
        record_types=None,
        limit,
        max_distance=None,
    ):
        calls.append((sources, record_types, limit, max_distance))
        if record_types:
            return [
                ChunkMatch(
                    chunk=_FakeChunk(
                        content="баланс",
                        title="CRM balance client #1",
                        source="hollihop",
                        external_id="balance:1",
                        record_type="balance",
                    ),
                    distance=0.21,
                )
            ]
        return []

    monkeypatch.setattr(retriever_mod, "session_scope", fake_scope)
    monkeypatch.setattr(retriever_mod, "search_chunk_matches", fake_search)

    openrouter = AsyncMock()
    openrouter.embed = AsyncMock(return_value=[[0.1, 0.2]])

    chunks = await VectorRetriever(openrouter).retrieve("какие долги по ученику", include_financial=True)

    assert chunks[0].record_type == "balance"
    assert calls[0][0] == ["hollihop"]
    assert "balance" in calls[0][1]


async def test_vector_retriever_prefers_schedule_chunks(monkeypatch) -> None:
    @asynccontextmanager
    async def fake_scope():
        yield object()

    async def fake_search(
        session,
        *,
        embedding,
        include_financial,
        sources=None,
        record_types=None,
        limit,
        max_distance=None,
    ):
        if record_types:
            return [
                ChunkMatch(
                    chunk=_FakeChunk(
                        content="урок завтра",
                        title="CRM edunit #1 — Group",
                        source="hollihop",
                        external_id="edunit:1",
                        record_type="schedule_item",
                    ),
                    distance=0.15,
                )
            ]
        return [
            ChunkMatch(
                chunk=_FakeChunk(
                    content="платеж",
                    title="CRM payment #1",
                    source="hollihop",
                    external_id="payment:1",
                    record_type="payment",
                ),
                distance=0.18,
            )
        ]

    monkeypatch.setattr(retriever_mod, "session_scope", fake_scope)
    monkeypatch.setattr(retriever_mod, "search_chunk_matches", fake_search)

    openrouter = AsyncMock()
    openrouter.embed = AsyncMock(return_value=[[0.1, 0.2]])

    chunks = await VectorRetriever(openrouter).retrieve("какие завтра уроки", include_financial=False)

    assert chunks[0].record_type == "schedule_item"
