from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from noteturner.db.models import SyncJob

ACTIVE_STATUSES = ("queued", "running")


async def enqueue_sync_job(
    session: AsyncSession,
    *,
    source: str,
    label: str,
    chat_id: int,
    status_message_id: int | None = None,
    record_types: tuple[str, ...] | None = None,
) -> SyncJob:
    job = SyncJob(
        source=source,
        label=label,
        chat_id=chat_id,
        status_message_id=status_message_id,
        record_types={"types": list(record_types)} if record_types is not None else None,
        status="queued",
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def get_active_job(session: AsyncSession, *, source: str) -> SyncJob | None:
    """Return a queued or running job for the source, if any (oldest first)."""
    result = await session.execute(
        select(SyncJob)
        .where(SyncJob.source == source, SyncJob.status.in_(ACTIVE_STATUSES))
        .order_by(SyncJob.requested_at)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def claim_next_job(session: AsyncSession) -> SyncJob | None:
    """Atomically claim the oldest queued job and mark it running.

    Uses ``FOR UPDATE SKIP LOCKED`` so multiple workers never grab the same job.
    """
    result = await session.execute(
        select(SyncJob)
        .where(SyncJob.status == "queued")
        .order_by(SyncJob.requested_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = result.scalar_one_or_none()
    if job is None:
        await session.rollback()
        return None
    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(job)
    return job


async def set_job_status_message(
    session: AsyncSession, job: SyncJob, *, message_id: int
) -> None:
    job.status_message_id = message_id
    await session.commit()


async def finish_job(
    session: AsyncSession,
    job: SyncJob,
    *,
    status: str,
    error_log: str | None = None,
) -> None:
    job.status = status
    job.error_log = error_log
    job.finished_at = datetime.now(timezone.utc)
    await session.commit()


def record_types_tuple(job: SyncJob) -> tuple[str, ...] | None:
    payload = job.record_types
    if not payload:
        return None
    types = payload.get("types") if isinstance(payload, dict) else None
    if not types:
        return None
    return tuple(types)
