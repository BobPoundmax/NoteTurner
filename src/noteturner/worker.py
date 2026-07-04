"""Background worker that executes queued sync jobs.

Run as a separate Render service (``type: worker``) with the same image as the
web service, e.g. ``python -m noteturner.worker``. It polls the ``sync_jobs``
table, claims one job at a time, runs the CRM/Drive sync, and reports progress
back to Telegram by editing the status message created when the job was
enqueued.

Running heavy sync here (instead of inline in the web process) means an
out-of-memory sync restarts only the worker, never the webhook bot.
"""
import asyncio
import contextlib
import logging
import signal

from noteturner.bot.dispatcher import create_bot
from noteturner.config.settings import Settings, get_settings
from noteturner.db.repositories.jobs import claim_next_job
from noteturner.db.session import close_db, init_db, session_scope
from noteturner.integrations.gdrive import GoogleDriveClient
from noteturner.integrations.hollihop import HollihopClient
from noteturner.integrations.openrouter import OpenRouterClient
from noteturner.services.sync_jobs import execute_sync_job

logger = logging.getLogger(__name__)


async def _claim() -> object | None:
    async with session_scope() as session:
        return await claim_next_job(session)


async def run_worker(settings: Settings, stop_event: asyncio.Event) -> None:
    init_db(settings)
    bot = create_bot(settings) if settings.telegram_bot_token else None
    if bot is None:
        logger.error("Worker cannot start: TELEGRAM_BOT_TOKEN is not set")
        return

    openrouter = OpenRouterClient(settings)
    hollihop = HollihopClient(settings)
    gdrive = GoogleDriveClient(settings)
    poll_interval = max(1.0, settings.sync_worker_poll_interval)

    logger.info("Sync worker started (poll interval %.1fs)", poll_interval)
    try:
        while not stop_event.is_set():
            try:
                row = await _claim()
            except Exception:  # noqa: BLE001 - keep polling despite transient DB errors
                logger.exception("Failed to claim next sync job")
                row = None

            if row is None:
                # Idle: wait for the poll interval or until asked to stop.
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
                continue

            logger.info("Claimed sync job id=%s source=%s label=%s", row.id, row.source, row.label)
            await execute_sync_job(row, bot, hollihop, openrouter, gdrive, settings)
            logger.info("Finished sync job id=%s", row.id)
    finally:
        logger.info("Sync worker shutting down")
        await close_db()
        with contextlib.suppress(Exception):
            await bot.session.close()


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, stop_event: asyncio.Event) -> None:
    def _request_stop() -> None:
        logger.info("Received shutdown signal; finishing current job then exiting")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            # Signal handlers are not available on some platforms (e.g. Windows).
            signal.signal(sig, lambda *_: _request_stop())


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    settings = get_settings()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    stop_event = asyncio.Event()
    _install_signal_handlers(loop, stop_event)
    try:
        loop.run_until_complete(run_worker(settings, stop_event))
    finally:
        loop.close()


if __name__ == "__main__":
    main()
