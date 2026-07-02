from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import Message

from noteturner.bot.utils import is_bot_mentioned, is_group_chat, is_private_chat, strip_bot_mention
from noteturner.config.settings import Settings
from noteturner.integrations.openrouter import OpenRouterClient, OpenRouterError

router = Router()


def _should_respond(message: Message, bot_username: str | None) -> bool:
    if is_private_chat(message):
        return True
    if is_group_chat(message):
        return is_bot_mentioned(message, bot_username)
    return False


@router.message(F.text, ~F.text.startswith("/"))
async def handle_text_message(
    message: Message,
    settings: Settings,
    openrouter: OpenRouterClient,
) -> None:
    if message.chat.type == ChatType.CHANNEL:
        return

    bot_info = await message.bot.get_me()
    if not _should_respond(message, bot_info.username):
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

    try:
        reply = await openrouter.chat_completion(
            [
                {
                    "role": "system",
                    "content": (
                        "Ты — корпоративный ассистент компании «Виртуозы». "
                        "Отвечай на русском языке, кратко и по делу."
                    ),
                },
                {"role": "user", "content": user_text},
            ]
        )
    except OpenRouterError as exc:
        await message.answer(f"Ошибка OpenRouter: {exc}")
        return

    await message.answer(reply)
