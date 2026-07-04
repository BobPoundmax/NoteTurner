from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import noteturner.services.sync_jobs as sj
from noteturner.db.repositories.jobs import record_types_tuple
from noteturner.services.crm_sync import CrmSyncResult


def _row(**kwargs):
    defaults = dict(
        id=1,
        source=sj.HOLLIHOP_SOURCE,
        label="данных CRM",
        record_types={"types": ["lead"]},
        chat_id=555,
        status_message_id=None,
        status="queued",
        started_at=None,
        requested_at=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_record_types_tuple_parses_payload() -> None:
    assert record_types_tuple(_row(record_types={"types": ["lead", "student"]})) == ("lead", "student")
    assert record_types_tuple(_row(record_types=None)) is None
    assert record_types_tuple(_row(record_types={})) is None


async def test_ensure_hollihop_enqueues_in_worker_mode(monkeypatch) -> None:
    @asynccontextmanager
    async def fake_scope():
        yield object()

    monkeypatch.setattr(sj, "get_settings", lambda: SimpleNamespace(sync_worker_enabled=True))
    monkeypatch.setattr(sj, "session_scope", fake_scope)
    monkeypatch.setattr(sj, "get_active_job", AsyncMock(return_value=None))
    enqueue_mock = AsyncMock(return_value=_row(status_message_id=42, started_at=None))
    monkeypatch.setattr(sj, "enqueue_sync_job", enqueue_mock)

    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=42))

    started, job = await sj.ensure_hollihop_sync_job(
        bot,
        555,
        AsyncMock(),
        AsyncMock(),
        label="данных CRM",
        record_types=("lead",),
    )

    assert started is True
    assert job.label == "данных CRM"
    assert job.status_message_id == 42
    assert enqueue_mock.await_args.kwargs["status_message_id"] == 42
    assert enqueue_mock.await_args.kwargs["record_types"] == ("lead",)


async def test_ensure_hollihop_skips_when_active_job_exists(monkeypatch) -> None:
    @asynccontextmanager
    async def fake_scope():
        yield object()

    monkeypatch.setattr(sj, "get_settings", lambda: SimpleNamespace(sync_worker_enabled=True))
    monkeypatch.setattr(sj, "session_scope", fake_scope)
    monkeypatch.setattr(sj, "get_active_job", AsyncMock(return_value=_row(status_message_id=7)))
    enqueue_mock = AsyncMock()
    monkeypatch.setattr(sj, "enqueue_sync_job", enqueue_mock)

    bot = AsyncMock()

    started, job = await sj.ensure_hollihop_sync_job(
        bot,
        555,
        AsyncMock(),
        AsyncMock(),
        label="данных CRM",
    )

    assert started is False
    assert job.status_message_id == 7
    enqueue_mock.assert_not_awaited()
    bot.send_message.assert_not_awaited()


async def test_execute_sync_job_finalizes_status(monkeypatch) -> None:
    finish_mock = AsyncMock()
    fake_session = SimpleNamespace(get=AsyncMock(return_value=object()))

    @asynccontextmanager
    async def fake_scope():
        yield fake_session

    monkeypatch.setattr(sj, "session_scope", fake_scope)
    monkeypatch.setattr(sj, "finish_job", finish_mock)
    run_mock = AsyncMock(return_value=CrmSyncResult(status="ok", records_processed=3, chunks_processed=5))
    monkeypatch.setattr(sj, "_run_hollihop_sync_job", run_mock)

    await sj.execute_sync_job(
        _row(id=99),
        AsyncMock(),
        AsyncMock(),
        AsyncMock(),
        AsyncMock(),
        SimpleNamespace(),
    )

    run_mock.assert_awaited_once()
    assert finish_mock.await_args.kwargs["status"] == "done"


async def test_execute_sync_job_marks_error_on_failed_result(monkeypatch) -> None:
    finish_mock = AsyncMock()
    fake_session = SimpleNamespace(get=AsyncMock(return_value=object()))

    @asynccontextmanager
    async def fake_scope():
        yield fake_session

    monkeypatch.setattr(sj, "session_scope", fake_scope)
    monkeypatch.setattr(sj, "finish_job", finish_mock)
    run_mock = AsyncMock(return_value=CrmSyncResult(status="error", error="boom"))
    monkeypatch.setattr(sj, "_run_hollihop_sync_job", run_mock)

    await sj.execute_sync_job(
        _row(id=100),
        AsyncMock(),
        AsyncMock(),
        AsyncMock(),
        AsyncMock(),
        SimpleNamespace(),
    )

    assert finish_mock.await_args.kwargs["status"] == "error"
    assert finish_mock.await_args.kwargs["error_log"] == "boom"
