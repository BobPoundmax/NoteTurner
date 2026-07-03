import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from aiogram import Bot

from noteturner.db.models import SyncRun
from noteturner.integrations.hollihop import HollihopClient
from noteturner.integrations.openrouter import OpenRouterClient
from noteturner.services.crm_sync import CrmSyncResult, run_hollihop_sync

logger = logging.getLogger(__name__)

HOLLIHOP_SOURCE = "hollihop"
_jobs_lock = asyncio.Lock()


@dataclass
class RunningSyncJob:
    source: str
    label: str
    started_at: datetime
    chat_id: int
    task: asyncio.Task[None]


_jobs: dict[str, RunningSyncJob] = {}


def _format_duration(total_seconds: int) -> str:
    minutes, seconds = divmod(max(total_seconds, 0), 60)
    hours, minutes = divmod(minutes, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    if not parts:
        parts.append(f"{seconds} сек")
    return " ".join(parts)


def format_running_sync_message(job: RunningSyncJob) -> str:
    elapsed = int((datetime.now(timezone.utc) - job.started_at).total_seconds())
    return (
        f"⏳ Выгрузка {job.label} из Hollihop всё ещё идёт "
        f"({_format_duration(elapsed)}). "
        "Напишу в этот чат, как только закончу."
    )


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
    bot: Bot,
    chat_id: int,
    hollihop: HollihopClient,
    openrouter: OpenRouterClient | None,
    *,
    label: str,
    record_types: tuple[str, ...] | None,
) -> None:
    try:
        result = await run_hollihop_sync(hollihop, openrouter, record_types=record_types)
        await bot.send_message(chat_id, format_finished_sync_message(label, result))
    except Exception:
        logger.exception("Background Hollihop sync crashed")
        await bot.send_message(
            chat_id,
            f"❌ Выгрузка {label} из Hollihop упала с необработанной ошибкой. "
            "Проверь логи приложения.",
        )
    finally:
        current_task = asyncio.current_task()
        async with _jobs_lock:
            existing = _jobs.get(HOLLIHOP_SOURCE)
            if existing is not None and existing.task is current_task:
                _jobs.pop(HOLLIHOP_SOURCE, None)


def get_running_hollihop_sync_job() -> RunningSyncJob | None:
    job = _jobs.get(HOLLIHOP_SOURCE)
    if job is None:
        return None
    if job.task.done():
        _jobs.pop(HOLLIHOP_SOURCE, None)
        return None
    return job


async def ensure_hollihop_sync_job(
    bot: Bot,
    chat_id: int,
    hollihop: HollihopClient,
    openrouter: OpenRouterClient | None,
    *,
    label: str,
    record_types: tuple[str, ...] | None = None,
) -> tuple[bool, RunningSyncJob]:
    async with _jobs_lock:
        existing = get_running_hollihop_sync_job()
        if existing is not None:
            return False, existing

        started_at = datetime.now(timezone.utc)
        task = asyncio.create_task(
            _run_hollihop_sync_job(
                bot,
                chat_id,
                hollihop,
                openrouter,
                label=label,
                record_types=record_types,
            )
        )
        job = RunningSyncJob(
            source=HOLLIHOP_SOURCE,
            label=label,
            started_at=started_at,
            chat_id=chat_id,
            task=task,
        )
        _jobs[HOLLIHOP_SOURCE] = job
        return True, job
