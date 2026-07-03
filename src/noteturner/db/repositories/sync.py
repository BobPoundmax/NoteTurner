from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from noteturner.db.models import RawRecord, SyncCursor, SyncRun


async def create_sync_run(session: AsyncSession, *, source: str) -> SyncRun:
    run = SyncRun(source=source, status="running")
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


async def finish_sync_run(
    session: AsyncSession,
    run: SyncRun,
    *,
    status: str,
    records_processed: int = 0,
    error_log: str | None = None,
) -> None:
    run.status = status
    run.records_processed = records_processed
    run.error_log = error_log
    run.finished_at = datetime.now(timezone.utc)
    await session.commit()


async def upsert_raw_record(
    session: AsyncSession,
    *,
    source: str,
    record_type: str,
    external_id: str | None,
    content: str,
    payload: dict | None,
    is_financial: bool = False,
) -> None:
    existing: RawRecord | None = None
    if external_id is not None:
        result = await session.execute(
            select(RawRecord).where(
                RawRecord.source == source,
                RawRecord.record_type == record_type,
                RawRecord.external_id == external_id,
            )
        )
        existing = result.scalar_one_or_none()

    if existing is None:
        session.add(
            RawRecord(
                source=source,
                record_type=record_type,
                external_id=external_id,
                content=content,
                payload=payload,
                is_financial=is_financial,
            )
        )
    else:
        existing.content = content
        existing.payload = payload
        existing.is_financial = is_financial
        existing.synced_at = datetime.now(timezone.utc)


async def get_raw_records(
    session: AsyncSession,
    *,
    include_financial: bool,
    limit: int = 20,
) -> list[RawRecord]:
    """Fetch raw records for retrieval. Financial records are hidden unless
    ``include_financial`` is True (i.e. the requester is an admin)."""
    stmt = select(RawRecord)
    if not include_financial:
        stmt = stmt.where(RawRecord.is_financial.is_(False))
    stmt = stmt.order_by(RawRecord.synced_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_raw_records(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(RawRecord))
    return int(result.scalar_one())


async def recent_sync_runs(session: AsyncSession, *, limit: int = 5) -> list[SyncRun]:
    result = await session.execute(
        select(SyncRun).order_by(SyncRun.started_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def latest_sync_run(
    session: AsyncSession,
    *,
    source: str,
    status: str | None = None,
) -> SyncRun | None:
    stmt = select(SyncRun).where(SyncRun.source == source)
    if status is not None:
        stmt = stmt.where(SyncRun.status == status)
    stmt = stmt.order_by(SyncRun.started_at.desc()).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_sync_cursor(
    session: AsyncSession,
    *,
    source: str,
    record_type: str,
) -> SyncCursor | None:
    result = await session.execute(
        select(SyncCursor).where(
            SyncCursor.source == source,
            SyncCursor.record_type == record_type,
        )
    )
    return result.scalar_one_or_none()


async def upsert_sync_cursor(
    session: AsyncSession,
    *,
    source: str,
    record_type: str,
    cursor_kind: str,
    cursor_value: str | None,
    meta: dict | None = None,
    last_records_processed: int = 0,
) -> SyncCursor:
    existing = await get_sync_cursor(session, source=source, record_type=record_type)
    if existing is None:
        existing = SyncCursor(
            source=source,
            record_type=record_type,
            cursor_kind=cursor_kind,
        )
        session.add(existing)

    existing.cursor_kind = cursor_kind
    existing.cursor_value = cursor_value
    existing.meta = meta
    existing.last_records_processed = last_records_processed
    existing.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(existing)
    return existing
