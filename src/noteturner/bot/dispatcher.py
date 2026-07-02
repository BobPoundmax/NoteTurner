import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from noteturner.bot.handlers import admin, messages, ping
from noteturner.config.settings import Settings
from noteturner.integrations.hollihop import HollihopClient
from noteturner.integrations.openrouter import OpenRouterClient

logger = logging.getLogger(__name__)


def create_bot(settings: Settings) -> Bot:
    return Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher(
    settings: Settings,
    openrouter: OpenRouterClient,
    hollihop: HollihopClient,
) -> Dispatcher:
    dp = Dispatcher()
    dp["settings"] = settings
    dp["openrouter"] = openrouter
    dp["hollihop"] = hollihop

    root_router = Router()
    root_router.include_router(ping.router)
    root_router.include_router(admin.router)
    root_router.include_router(messages.router)
    dp.include_router(root_router)

    return dp


async def setup_webhook(bot: Bot, settings: Settings) -> None:
    if settings.bot_mode != "webhook":
        return
    if not settings.webhook_base_url or not settings.telegram_webhook_secret:
        logger.warning("Webhook URL not configured, skipping setWebhook")
        return

    await bot.set_webhook(
        url=settings.webhook_url,
        drop_pending_updates=True,
        allowed_updates=["message", "edited_message"],
    )
    logger.info("Webhook set to %s", settings.webhook_url)


async def remove_webhook(bot: Bot) -> None:
    await bot.delete_webhook(drop_pending_updates=False)
