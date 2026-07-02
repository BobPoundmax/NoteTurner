from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from noteturner.config.settings import Settings
from noteturner.db.session import check_database
from noteturner.integrations.hollihop import HollihopClient
from noteturner.integrations.openrouter import OpenRouterClient

router = Router()


def _format_check(name: str, result: dict) -> str:
    status = result.get("status", "unknown")
    if status == "ok":
        extra = ""
        if "latency_ms" in result:
            extra = f" ({result['latency_ms']} ms)"
        if "locations_count" in result:
            extra = f" ({result['locations_count']} локаций)"
        return f"✅ {name}{extra}"
    if status == "skipped":
        return f"⏭ {name}: {result.get('error', 'not configured')}"
    return f"❌ {name}: {result.get('error', 'unknown error')}"


@router.message(Command("status"))
async def cmd_status(
    message: Message,
    settings: Settings,
    openrouter: OpenRouterClient,
    hollihop: HollihopClient,
) -> None:
    if settings.admin_telegram_id <= 0 or message.from_user is None:
        await message.answer("Команда /status недоступна.")
        return
    if message.from_user.id != settings.admin_telegram_id:
        await message.answer("Команда /status доступна только администратору.")
        return

    db_result = await check_database()
    or_result = await openrouter.health_check()
    hh_result = await hollihop.health_check() if hollihop.is_configured else {
        "status": "skipped",
        "error": "HOLLIHOP_SUBDOMAIN / HOLLIHOP_AUTH_KEY not set",
    }

    bot_info = await message.bot.get_me()
    lines = [
        "<b>Статус Note Turner</b>",
        "",
        _format_check("Database", db_result),
        _format_check("OpenRouter", or_result),
        _format_check("Hollihop CRM", hh_result),
        "",
        f"Bot: @{bot_info.username}",
        f"Mode: {settings.bot_mode}",
    ]
    await message.answer("\n".join(lines))
