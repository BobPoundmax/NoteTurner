from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import noteturner.services.drive_sync as ds
from noteturner.integrations.gdrive import MIME_DOC, DriveFile
from noteturner.services.drive_sync import chunk_text, is_financial_name


def test_chunk_text_empty_returns_no_chunks() -> None:
    assert chunk_text("   ") == []


def test_chunk_text_splits_with_overlap() -> None:
    text = "a" * 2500
    chunks = chunk_text(text, size=1000, overlap=150)
    assert len(chunks) > 1
    assert all(len(c) <= 1000 for c in chunks)


def test_is_financial_name() -> None:
    assert is_financial_name("Финансовый отчёт 2026", ["финанс"]) is True
    assert is_financial_name("Marketing plan", ["финанс", "payment"]) is False


async def test_sync_file_embeds_and_stores(monkeypatch) -> None:
    @asynccontextmanager
    async def fake_scope():
        yield object()

    replace_mock = AsyncMock(return_value=0)
    monkeypatch.setattr(ds, "session_scope", fake_scope)
    monkeypatch.setattr(ds, "replace_file_chunks", replace_mock)

    gdrive = AsyncMock()
    gdrive.extract_text = AsyncMock(return_value="слово " * 600)

    openrouter = AsyncMock()

    async def fake_embed(batch, **kwargs):
        return [[0.1, 0.2, 0.3] for _ in batch]

    openrouter.embed = AsyncMock(side_effect=fake_embed)

    file = DriveFile(id="f1", name="Report", mime_type=MIME_DOC)
    count = await ds._sync_file(gdrive, openrouter, file, financial_keywords=[])

    assert count > 0
    replace_mock.assert_awaited_once()
    kwargs = replace_mock.await_args.kwargs
    assert kwargs["source"] == "gdrive"
    assert kwargs["external_id"] == "f1"
    assert len(kwargs["chunks"]) == count
    assert kwargs["chunks"][0].record_type == "doc"
