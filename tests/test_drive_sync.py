from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import noteturner.services.drive_sync as ds
from noteturner.config.settings import Settings
from noteturner.integrations.gdrive import MIME_DOC, MIME_SHEET, DriveFile, DriveListResult
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

    delete_mock = AsyncMock()

    def _add(session, *, source, external_id, chunks):
        return len(chunks)

    add_mock = AsyncMock(side_effect=_add)
    monkeypatch.setattr(ds, "session_scope", fake_scope)
    monkeypatch.setattr(ds, "delete_source_chunks", delete_mock)
    monkeypatch.setattr(ds, "add_chunks", add_mock)

    gdrive = AsyncMock()
    gdrive.extract_text = AsyncMock(return_value="слово " * 600)

    openrouter = AsyncMock()

    async def fake_embed(batch, **kwargs):
        return [[0.1, 0.2, 0.3] for _ in batch]

    openrouter.embed = AsyncMock(side_effect=fake_embed)

    file = DriveFile(id="f1", name="Report", mime_type=MIME_DOC)
    count = await ds._sync_file(gdrive, openrouter, file, financial_keywords=[])

    assert count > 0
    delete_mock.assert_awaited_once()
    add_mock.assert_awaited()

    # Old chunks are cleared before any new batch is written.
    all_calls = add_mock.await_args_list
    stored = sum(len(call.kwargs["chunks"]) for call in all_calls)
    assert stored == count
    assert all_calls[0].kwargs["source"] == "gdrive"
    assert all_calls[0].kwargs["external_id"] == "f1"
    assert all_calls[0].kwargs["chunks"][0].record_type == "doc"

    # chunk_index should be contiguous across batches.
    indexes = [chunk.chunk_index for call in all_calls for chunk in call.kwargs["chunks"]]
    assert indexes == list(range(count))


async def test_run_drive_sync_reports_count_and_processing(monkeypatch) -> None:
    class _FakeSession:
        async def get(self, *_args, **_kwargs):
            return object()

    @asynccontextmanager
    async def fake_scope():
        yield _FakeSession()

    monkeypatch.setattr(ds, "session_scope", fake_scope)
    monkeypatch.setattr(ds, "create_sync_run", AsyncMock(return_value=SimpleNamespace(id=7)))
    monkeypatch.setattr(ds, "finish_sync_run", AsyncMock())
    monkeypatch.setattr(ds, "_sync_file", AsyncMock(side_effect=[2, 0]))

    gdrive = AsyncMock()
    gdrive.is_configured = True
    gdrive.list_files_detailed = AsyncMock(
        return_value=DriveListResult(
            files=[
                DriveFile(id="f1", name="Budget 2026", mime_type=MIME_DOC),
                DriveFile(id="f2", name="Students", mime_type=MIME_SHEET),
            ],
            roots=[],
            skipped_by_mime={},
        )
    )

    openrouter = AsyncMock()
    openrouter.is_configured = True
    settings = Settings(financial_keywords="budget")

    progress_updates = []

    async def progress(update) -> None:
        progress_updates.append(update)

    result = await ds.run_drive_sync(gdrive, openrouter, settings, progress=progress)

    assert result.status == "ok"
    assert result.files_discovered == 2
    assert result.files_processed == 2
    assert result.chunks_processed == 2
    assert result.financial_files == 1
    assert result.per_type == {"doc": 1, "sheet": 1}
    assert [update.stage for update in progress_updates] == [
        "discovery",
        "processing",
        "processing",
    ]
    assert progress_updates[0].total_files == 2
    assert "Найдено 2 файлов" in (progress_updates[0].message or "")
    assert progress_updates[1].current_index == 1
    assert progress_updates[1].file_name == "Budget 2026"
    assert progress_updates[2].current_index == 2
    assert progress_updates[2].file_name == "Students"
