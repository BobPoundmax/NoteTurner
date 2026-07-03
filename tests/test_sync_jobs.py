from datetime import datetime, timezone

from noteturner.db.models import SyncRun
from noteturner.services.crm_sync import CrmSyncProgress
from noteturner.services.crm_sync import CrmSyncResult
from noteturner.services.sync_jobs import (
    RunningSyncJob,
    format_finished_sync_message,
    format_last_sync_message,
    format_running_sync_message,
)


def test_format_last_sync_message_for_successful_run() -> None:
    run = SyncRun(
        source="hollihop",
        status="ok",
        records_processed=42,
        started_at=datetime(2026, 7, 3, 17, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 7, 3, 17, 5, tzinfo=timezone.utc),
    )

    message = format_last_sync_message(run)

    assert "Последняя CRM-выгрузка завершилась" in message
    assert "42" in message


def test_format_finished_sync_message_for_partial_vectorization_error() -> None:
    result = CrmSyncResult(
        status="ok",
        records_processed=10,
        financial_processed=4,
        chunks_processed=8,
        per_type={"payment": 10},
        error="payment: vectorization failed (timeout)",
    )

    message = format_finished_sync_message("платежей", result)

    assert "Выгрузка платежей из Hollihop завершена" in message
    assert "payment: 10" in message
    assert "vectorization failed" in message


def test_format_finished_sync_message_falls_back_for_empty_error() -> None:
    result = CrmSyncResult(status="error", error="")

    message = format_finished_sync_message("данных CRM", result)

    assert "неизвестная ошибка" in message


def test_format_running_sync_message_includes_latest_progress() -> None:
    job = RunningSyncJob(
        source="hollihop",
        label="данных CRM",
        started_at=datetime.now(timezone.utc),
        chat_id=42,
        last_progress=CrmSyncProgress(
            stage="page_fetched",
            record_type="lead",
            records_processed=200,
            page_index=2,
            message="⏳ CRM: загружено 200 записей типа lead (2 стр., последняя +100).",
        ),
    )

    message = format_running_sync_message(job)

    assert "данных CRM" in message
    assert "загружено 200 записей типа lead" in message
    assert "раз в минуту" in message
