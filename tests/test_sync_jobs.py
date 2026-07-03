from datetime import datetime, timezone

from noteturner.db.models import SyncRun
from noteturner.services.crm_sync import CrmSyncResult
from noteturner.services.sync_jobs import format_finished_sync_message, format_last_sync_message


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
