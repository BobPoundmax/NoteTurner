import logging

from aiogram import F, Router
from aiogram.types import Message

from noteturner.bot.access import is_admin
from noteturner.bot.filters import ChatRoleFilter
from noteturner.bot.utils import is_bot_mentioned, is_group_chat, strip_bot_mention
from noteturner.config.settings import Settings
from noteturner.db.repositories.query_logs import add_query_log
from noteturner.db.session import session_scope
from noteturner.integrations.openrouter import OpenRouterClient, OpenRouterError
from noteturner.services.llm.answerer import Answerer
from noteturner.services.llm.retriever import VectorRetriever

logger = logging.getLogger(__name__)

router = Router()


async def _log_query(telegram_chat_id: int, question: str, model: str | None) -> None:
    try:
        async with session_scope() as session:
            await add_query_log(
                session,
                telegram_chat_id=telegram_chat_id,
                question=question,
                model=model,
            )
    except RuntimeError:
        pass
    except Exception:
        logger.exception("Failed to log query for chat %s", telegram_chat_id)


@router.message(F.text, ~F.text.startswith("/"), ChatRoleFilter("assistant"))
async def handle_assistant_message(
    message: Message,
    settings: Settings,
    openrouter: OpenRouterClient,
) -> None:
    bot_info = await message.bot.get_me()
    if is_group_chat(message) and not is_bot_mentioned(message, bot_info.username):
        return

    user_text = strip_bot_mention(message.text or "", bot_info.username)
    if not user_text:
        await message.answer("Напишите ваш вопрос после упоминания @бота.")
        return

    await message.bot.send_chat_action(message.chat.id, "typing")

    if not openrouter.is_configured:
        await message.answer(
            "OpenRouter не настроен. Администратор должен задать <code>OPENROUTER_API_KEY</code>."
        )
        return

    user_id = message.from_user.id if message.from_user else None
    admin = await is_admin(user_id, settings)

    retriever = VectorRetriever(openrouter) if settings.gdrive_is_configured else None

    try:
        result = await Answerer(openrouter, retriever=retriever).answer(user_text, is_admin=admin)
    except OpenRouterError as exc:
        await message.answer(f"Ошибка OpenRouter: {exc}")
        return

    await message.answer(result.text)
    await _log_query(message.chat.id, user_text, result.model)
