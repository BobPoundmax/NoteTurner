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

# Phrases signalling that an admin wants to trigger data collection/sync.
# Handled directly (pointing to /admin) instead of guessing via the LLM.
SYNC_INTENT_PHRASES: tuple[str, ...] = (
    "сбор данных",
    "собрать данные",
    "собери данные",
    "запусти сбор",
    "запустить сбор",
    "обнови данные",
    "обновить данные",
    "обнови crm",
    "обновить crm",
    "загрузи crm",
    "выгрузи crm",
    "загрузи google drive",
    "загрузи диск",
    "синхрониз",
)

SYNC_INTENT_REPLY = (
    "Сбор данных запускается вручную из меню <b>/admin</b>:\n"
    "• <b>Загрузить CRM</b> — данные Hollihop (лиды, ученики, платежи);\n"
    "• <b>Загрузить Google Drive</b> — документы и таблицы.\n\n"
    "После загрузки данные векторизуются и я смогу опираться на них в ответах. "
    "Автоматической синхронизации по расписанию пока нет."
)


def is_sync_intent(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in SYNC_INTENT_PHRASES)


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

    if admin and is_sync_intent(user_text):
        await message.answer(SYNC_INTENT_REPLY)
        return

    # doc_chunks may hold Google Drive and/or CRM vectors, so retrieve whenever
    # embeddings are available (OpenRouter is already confirmed configured above).
    retriever = VectorRetriever(openrouter)

    try:
        result = await Answerer(openrouter, retriever=retriever).answer(user_text, is_admin=admin)
    except OpenRouterError as exc:
        await message.answer(f"Ошибка OpenRouter: {exc}")
        return

    await message.answer(result.text)
    await _log_query(message.chat.id, user_text, result.model)
