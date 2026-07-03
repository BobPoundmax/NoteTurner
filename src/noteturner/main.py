import asyncio
import logging
from contextlib import asynccontextmanager

from aiogram.types import Update
from fastapi import FastAPI, HTTPException, Request, Response

from noteturner.bot.dispatcher import create_bot, create_dispatcher, remove_webhook, setup_webhook
from noteturner.bot.middlewares.inject import InjectDependenciesMiddleware
from noteturner.config.settings import Settings, get_settings
from noteturner.db.session import close_db, init_db
from noteturner.health.checker import run_health_checks
from noteturner.integrations.gdrive import GoogleDriveClient
from noteturner.integrations.hollihop import HollihopClient
from noteturner.integrations.openrouter import OpenRouterClient

logger = logging.getLogger(__name__)

settings: Settings = get_settings()
bot = create_bot(settings) if settings.telegram_bot_token else None
openrouter = OpenRouterClient(settings)
hollihop = HollihopClient(settings)
gdrive = GoogleDriveClient(settings)
dp = create_dispatcher(settings, openrouter, hollihop, gdrive)
dp.update.middleware(InjectDependenciesMiddleware(settings, openrouter, hollihop, gdrive))

_polling_task: asyncio.Task | None = None


async def _run_polling() -> None:
    if bot is None:
        logger.error("Cannot start polling: TELEGRAM_BOT_TOKEN not set")
        return
    logger.info("Starting bot in polling mode")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _polling_task

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    init_db(settings)

    if bot is not None:
        if settings.bot_mode == "polling":
            _polling_task = asyncio.create_task(_run_polling())
        else:
            await setup_webhook(bot, settings)
    else:
        logger.warning("TELEGRAM_BOT_TOKEN not set — bot disabled")

    yield

    if _polling_task is not None:
        _polling_task.cancel()
        try:
            await _polling_task
        except asyncio.CancelledError:
            pass

    if bot is not None and settings.bot_mode == "webhook":
        await remove_webhook(bot)
    await close_db()
    if bot is not None:
        await bot.session.close()


app = FastAPI(title="Note Turner", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    result = await run_health_checks(
        bot=bot, openrouter=openrouter, hollihop=hollihop, gdrive=gdrive
    )
    if not result.get("critical_ok", True):
        raise HTTPException(status_code=503, detail=result)
    return result


@app.post("/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request) -> Response:
    if secret != settings.telegram_webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    if bot is None:
        raise HTTPException(status_code=503, detail="Bot not configured")

    payload = await request.json()
    update = Update.model_validate(payload, context={"bot": bot})
    await dp.feed_update(bot, update)
    return Response(status_code=200)
