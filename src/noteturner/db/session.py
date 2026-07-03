import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from noteturner.config.settings import Settings
from noteturner.db.models import SyncRun
from noteturner.db.repositories.sync import latest_sync_run

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_db(settings: Settings) -> None:
    global _engine, _session_factory
    if not settings.database_url:
        return
    _engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def close_db() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def get_session_factory() -> async_sessionmaker[AsyncSession] | None:
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Database is not configured (DATABASE_URL not set)")
    async with _session_factory() as session:
        yield session


def _serialize_sync_run(run: SyncRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "status": run.status,
        "records_processed": run.records_processed,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "error_log": run.error_log,
    }


async def _load_sync_health(session: AsyncSession) -> dict[str, dict[str, Any] | None]:
    syncs: dict[str, dict[str, Any] | None] = {}
    for source in ("hollihop", "gdrive"):
        last_run = await latest_sync_run(session, source=source)
        last_success = await latest_sync_run(session, source=source, status="ok")
        syncs[source] = {
            "last_run": _serialize_sync_run(last_run),
            "last_success_at": (
                last_success.finished_at.isoformat()
                if last_success is not None and last_success.finished_at is not None
                else None
            ),
        }
    return syncs


async def check_database() -> dict[str, Any]:
    if _engine is None:
        return {"status": "skipped", "error": "DATABASE_URL not configured"}

    try:
        async with _engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        if _session_factory is None:
            return {"status": "ok"}
        async with _session_factory() as session:
            sync_health = await _load_sync_health(session)
        return {"status": "ok", "sync_runs": sync_health}
    except Exception as exc:
        logger.exception("Database health check failed")
        return {"status": "error", "error": str(exc)}
