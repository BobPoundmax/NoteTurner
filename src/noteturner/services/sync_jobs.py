import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from aiogram import Bot

from noteturner.config.settings import Settings, get_settings
from noteturner.db.models import SyncJob, SyncRun
from noteturner.db.repositories.jobs import (
    enqueue_sync_job,
    finish_job,
    get_active_job,
    record_types_tuple,
)
from noteturner.db.session import session_scope
from noteturner.integrations.gdrive import GoogleDriveClient
from noteturner.integrations.hollihop import HollihopClient
from noteturner.integrations.openrouter import OpenRouterClient
from noteturner.services.crm_sync import CrmSyncProgress, CrmSyncResult, run_hollihop_sync
from noteturner.services.drive_sync import DriveSyncProgress, DriveSyncResult, run_drive_sync

logger = logging.getLogger(__name__)

HOLLIHOP_SOURCE = "hollihop"
GDRIVE_SOURCE = "gdrive"
_jobs_lock = asyncio.Lock()


@dataclass
class RunningSyncJob:
    source: str
    label: str
    started_at: datetime
    chat_id: int
    task: asyncio.Task[None] | None = None
    last_progress: Any | None = None
    status_message_id: int | None = None
    last_status_text: str | None = None
    last_status_update_at: datetime | None = None


_jobs: dict[str, RunningSyncJob] = {}


def _format_duration(total_seconds: int) -> str:
    minutes, seconds = divmod(max(total_seconds, 0), 60)
    hours, minutes = divmod(minutes, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    if seconds or not parts:
        parts.append(f"{seconds} сек")
    return " ".join(parts)


def format_running_sync_message(job: RunningSyncJob) -> str:
    elapsed = int((datetime.now(timezone.utc) - job.started_at).total_seconds())
    lines = [
        f"⏳ Выгрузка {job.label} из Hollihop всё ещё идёт "
        f"({_format_duration(elapsed)})."
    ]
    progress_message = getattr(job.last_progress, "message", None)
    if progress_message:
        lines.append(progress_message)
    lines.append("Обновляю этот статус раз в минуту, пока идёт загрузка.")
    return "\n".join(lines)


def format_running_drive_sync_message(job: RunningSyncJob) -> str:
    elapsed = int((datetime.now(timezone.utc) - job.started_at).total_seconds())
    lines = [
        f"⏳ Загрузка {job.label} всё ещё идёт "
        f"({_format_duration(elapsed)})."
    ]
    progress_message = getattr(job.last_progress, "message", None)
    if progress_message:
        lines.append(progress_message)
    lines.append("Обновляю этот статус раз в минуту и при смене этапов.")
    return "\n".join(lines)


async def _upsert_status_message(
    bot: Bot,
    job: RunningSyncJob,
    text: str,
    *,
    allow_send: bool = True,
) -> None:
    if text == job.last_status_text:
        return

    if job.status_message_id is None:
        if not allow_send:
            return
        message = await bot.send_message(job.chat_id, text)
        job.status_message_id = message.message_id
        job.last_status_text = text
        job.last_status_update_at = datetime.now(timezone.utc)
        return

    try:
        await bot.edit_message_text(text, chat_id=job.chat_id, message_id=job.status_message_id)
        job.last_status_text = text
        job.last_status_update_at = datetime.now(timezone.utc)
    except Exception:
        logger.exception("Failed to edit sync status message")
        if allow_send:
            message = await bot.send_message(job.chat_id, text)
            job.status_message_id = message.message_id
            job.last_status_text = text
            job.last_status_update_at = datetime.now(timezone.utc)


async def _post_periodic_status_updates(
    bot: Bot,
    job: RunningSyncJob,
    formatter,
    *,
    interval_seconds: float = 60.0,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        await _upsert_status_message(bot, job, formatter(job), allow_send=False)


def format_finished_sync_message(label: str, result: CrmSyncResult) -> str:
    if result.status == "ok":
        by_type = ", ".join(f"{key}: {value}" for key, value in result.per_type.items()) or "нет записей"
        note = f"\n⚠️ {result.error}" if result.error else ""
        return (
            f"✅ Выгрузка {label} из Hollihop завершена. "
            f"Обработано {result.records_processed} "
            f"(из них финансовых {result.financial_processed}), "
            f"векторизовано {result.chunks_processed}. {by_type}.{note}"
        )
    return f"❌ Ошибка выгрузки {label} из Hollihop: {result.error or 'неизвестная ошибка'}"


def format_finished_drive_sync_message(label: str, result: DriveSyncResult) -> str:
    if result.status == "ok":
        by_type = ", ".join(f"{key}: {value}" for key, value in result.per_type.items()) or "нет файлов"
        note = f"\n⚠️ Часть файлов пропущена: {result.error}" if result.error else ""
        hint = f"\n\n{result.hint}" if result.hint else ""
        return (
            f"✅ Загрузка {label} завершена. Найдено {result.files_discovered}, "
            f"обработано {result.files_processed}, чанков {result.chunks_processed} "
            f"(финансовых файлов {result.financial_files}). {by_type}.{note}{hint}"
        )
    return f"❌ Ошибка загрузки {label}: {result.error or 'неизвестная ошибка'}"


def format_last_sync_message(run: SyncRun | None) -> str:
    if run is None:
        return "По CRM ещё не было завершённых выгрузок."

    finished_at = run.finished_at.astimezone().strftime("%d.%m %H:%M") if run.finished_at else "ещё не завершена"
    if run.status == "running":
        return (
            "⏳ Последняя CRM-выгрузка всё ещё помечена как запущенная "
            f"(старт {run.started_at.astimezone().strftime('%d.%m %H:%M')})."
        )
    if run.status == "ok":
        return (
            f"✅ Последняя CRM-выгрузка завершилась {finished_at}. "
            f"Обработано {run.records_processed} записей."
        )
    return (
        f"❌ Последняя CRM-выгрузка завершилась ошибкой {finished_at}: "
        f"{run.error_log or 'неизвестная ошибка'}"
    )


async def _run_hollihop_sync_job(
    job: RunningSyncJob,
    bot: Bot,
    hollihop: HollihopClient,
    openrouter: OpenRouterClient | None,
    *,
    label: str,
    record_types: tuple[str, ...] | None,
) -> CrmSyncResult | None:
    ticker_task: asyncio.Task[None] | None = None
    result: CrmSyncResult | None = None

    async def report_progress(update: CrmSyncProgress) -> None:
        previous_stage = job.last_progress.stage if job.last_progress is not None else None
        job.last_progress = update
        now = datetime.now(timezone.utc)
        last_update_at = job.last_status_update_at
        should_refresh = (
            last_update_at is None
            or update.stage != previous_stage
            or (now - last_update_at).total_seconds() >= 60
        )
        if should_refresh:
            await _upsert_status_message(bot, job, format_running_sync_message(job), allow_send=False)

    try:
        await _upsert_status_message(bot, job, format_running_sync_message(job))
        ticker_task = asyncio.create_task(_post_periodic_status_updates(bot, job, format_running_sync_message))
        result = await run_hollihop_sync(
            hollihop,
            openrouter,
            record_types=record_types,
            progress=report_progress,
        )
        await _upsert_status_message(
            bot,
            job,
            format_finished_sync_message(label, result),
        )
    except Exception:
        logger.exception("Background Hollihop sync crashed")
        await _upsert_status_message(
            bot,
            job,
            f"❌ Выгрузка {label} из Hollihop упала с необработанной ошибкой. "
            "Проверь логи приложения.",
        )
    finally:
        if ticker_task is not None:
            ticker_task.cancel()
            try:
                await ticker_task
            except asyncio.CancelledError:
                pass

        current_task = asyncio.current_task()
        async with _jobs_lock:
            existing = _jobs.get(HOLLIHOP_SOURCE)
            if existing is not None and existing.task is current_task:
                _jobs.pop(HOLLIHOP_SOURCE, None)

    return result


async def _run_drive_sync_job(
    job: RunningSyncJob,
    bot: Bot,
    gdrive: GoogleDriveClient,
    openrouter: OpenRouterClient,
    settings: Settings,
    *,
    label: str,
) -> DriveSyncResult | None:
    ticker_task: asyncio.Task[None] | None = None
    result: DriveSyncResult | None = None

    async def report_progress(update: DriveSyncProgress) -> None:
        previous_stage = getattr(job.last_progress, "stage", None)
        job.last_progress = update
        now = datetime.now(timezone.utc)
        last_update_at = job.last_status_update_at
        should_refresh = (
            last_update_at is None
            or update.stage != previous_stage
            or (now - last_update_at).total_seconds() >= 60
        )
        if should_refresh:
            await _upsert_status_message(bot, job, format_running_drive_sync_message(job), allow_send=False)

    try:
        await _upsert_status_message(bot, job, format_running_drive_sync_message(job))
        ticker_task = asyncio.create_task(_post_periodic_status_updates(bot, job, format_running_drive_sync_message))
        result = await run_drive_sync(
            gdrive,
            openrouter,
            settings,
            progress=report_progress,
        )
        await _upsert_status_message(bot, job, format_finished_drive_sync_message(label, result))
    except Exception:
        logger.exception("Background Google Drive sync crashed")
        await _upsert_status_message(
            bot,
            job,
            f"❌ Загрузка {label} упала с необработанной ошибкой. Проверь логи приложения.",
        )
    finally:
        if ticker_task is not None:
            ticker_task.cancel()
            try:
                await ticker_task
            except asyncio.CancelledError:
                pass

        current_task = asyncio.current_task()
        async with _jobs_lock:
            existing = _jobs.get(GDRIVE_SOURCE)
            if existing is not None and existing.task is current_task:
                _jobs.pop(GDRIVE_SOURCE, None)

    return result


def get_running_hollihop_sync_job() -> RunningSyncJob | None:
    job = _jobs.get(HOLLIHOP_SOURCE)
    if job is None:
        return None
    if job.task.done():
        _jobs.pop(HOLLIHOP_SOURCE, None)
        return None
    return job


def get_running_drive_sync_job() -> RunningSyncJob | None:
    job = _jobs.get(GDRIVE_SOURCE)
    if job is None:
        return None
    if job.task.done():
        _jobs.pop(GDRIVE_SOURCE, None)
        return None
    return job


def _running_job_from_row(row: SyncJob) -> RunningSyncJob:
    return RunningSyncJob(
        source=row.source,
        label=row.label,
        started_at=row.started_at or row.requested_at or datetime.now(timezone.utc),
        chat_id=row.chat_id,
        status_message_id=row.status_message_id,
    )


async def _enqueue_sync_job(
    bot: Bot,
    chat_id: int,
    *,
    source: str,
    label: str,
    record_types: tuple[str, ...] | None,
    queued_text: str,
) -> tuple[bool, RunningSyncJob]:
    """Enqueue a job for the worker process instead of running it inline."""
    async with session_scope() as session:
        existing = await get_active_job(session, source=source)
        if existing is not None:
            return False, _running_job_from_row(existing)

    status_message_id: int | None = None
    try:
        message = await bot.send_message(chat_id, queued_text)
        status_message_id = message.message_id
    except Exception:
        logger.exception("Failed to send queued status message")

    async with session_scope() as session:
        row = await enqueue_sync_job(
            session,
            source=source,
            label=label,
            chat_id=chat_id,
            status_message_id=status_message_id,
            record_types=record_types,
        )
        return True, _running_job_from_row(row)


async def ensure_hollihop_sync_job(
    bot: Bot,
    chat_id: int,
    hollihop: HollihopClient,
    openrouter: OpenRouterClient | None,
    *,
    label: str,
    record_types: tuple[str, ...] | None = None,
) -> tuple[bool, RunningSyncJob]:
    if get_settings().sync_worker_enabled:
        return await _enqueue_sync_job(
            bot,
            chat_id,
            source=HOLLIHOP_SOURCE,
            label=label,
            record_types=record_types,
            queued_text=(
                f"🗂️ Выгрузка {label} из Hollihop поставлена в очередь. "
                "Её обработает отдельный воркер, статус буду обновлять здесь."
            ),
        )

    async with _jobs_lock:
        existing = get_running_hollihop_sync_job()
        if existing is not None:
            return False, existing

        started_at = datetime.now(timezone.utc)
        job = RunningSyncJob(
            source=HOLLIHOP_SOURCE,
            label=label,
            started_at=started_at,
            chat_id=chat_id,
        )
        task = asyncio.create_task(
            _run_hollihop_sync_job(
                job,
                bot,
                hollihop,
                openrouter,
                label=label,
                record_types=record_types,
            )
        )
        job.task = task
        _jobs[HOLLIHOP_SOURCE] = job
        return True, job


async def ensure_drive_sync_job(
    bot: Bot,
    chat_id: int,
    gdrive: GoogleDriveClient,
    openrouter: OpenRouterClient,
    settings: Settings,
    *,
    label: str = "Google Drive",
) -> tuple[bool, RunningSyncJob]:
    if get_settings().sync_worker_enabled:
        return await _enqueue_sync_job(
            bot,
            chat_id,
            source=GDRIVE_SOURCE,
            label=label,
            record_types=None,
            queued_text=(
                f"🗂️ Загрузка {label} поставлена в очередь. "
                "Её обработает отдельный воркер, статус буду обновлять здесь."
            ),
        )

    async with _jobs_lock:
        existing = get_running_drive_sync_job()
        if existing is not None:
            return False, existing

        started_at = datetime.now(timezone.utc)
        job = RunningSyncJob(
            source=GDRIVE_SOURCE,
            label=label,
            started_at=started_at,
            chat_id=chat_id,
        )
        task = asyncio.create_task(
            _run_drive_sync_job(
                job,
                bot,
                gdrive,
                openrouter,
                settings,
                label=label,
            )
        )
        job.task = task
        _jobs[GDRIVE_SOURCE] = job
        return True, job


async def execute_sync_job(
    row: SyncJob,
    bot: Bot,
    hollihop: HollihopClient,
    openrouter: OpenRouterClient | None,
    gdrive: GoogleDriveClient,
    settings: Settings,
) -> None:
    """Run a claimed queue job in the worker process and finalize its DB row.

    Reuses the same status-reporting runners as the in-process path so the
    Telegram status message (created when the job was enqueued) is edited in
    place.
    """
    job = _running_job_from_row(row)
    status = "error"
    error_log: str | None = None

    try:
        if row.source == HOLLIHOP_SOURCE:
            result = await _run_hollihop_sync_job(
                job,
                bot,
                hollihop,
                openrouter,
                label=row.label,
                record_types=record_types_tuple(row),
            )
            if result is None:
                status, error_log = "error", "sync crashed (see worker logs)"
            else:
                status = "done" if result.status == "ok" else "error"
                error_log = result.error
        elif row.source == GDRIVE_SOURCE:
            if openrouter is None:
                status, error_log = "error", "OpenRouter is not configured"
            else:
                result = await _run_drive_sync_job(
                    job,
                    bot,
                    gdrive,
                    openrouter,
                    settings,
                    label=row.label,
                )
                if result is None:
                    status, error_log = "error", "sync crashed (see worker logs)"
                else:
                    status = "done" if result.status == "ok" else "error"
                    error_log = result.error
        else:
            status, error_log = "error", f"unknown sync source {row.source}"
    except Exception as exc:  # noqa: BLE001 - never let one job kill the worker loop
        logger.exception("Worker sync job %s (%s) crashed", row.id, row.source)
        status, error_log = "error", str(exc)
    finally:
        async with session_scope() as session:
            db_job = await session.get(SyncJob, row.id)
            if db_job is not None:
                await finish_job(session, db_job, status=status, error_log=error_log)
