import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from noteturner.bot.access import is_admin
from noteturner.bot.utils import is_private_chat
from noteturner.config.settings import Settings
from noteturner.db.repositories.chats import get_chat_by_telegram_id
from noteturner.db.session import session_scope

logger = logging.getLogger(__name__)


async def _resolve_role(telegram_chat_id: int) -> str | None:
    try:
        async with session_scope() as session:
            chat = await get_chat_by_telegram_id(session, telegram_chat_id)
            return chat.role if chat else None
    except RuntimeError:
        return None
    except Exception:
        logger.exception("Failed to resolve chat role for %s", telegram_chat_id)
        return None


class ChatAccessMiddleware(BaseMiddleware):
    """Resolve the chat role and gate access for unregistered chats.

    Registered chats get ``chat_role`` injected. Unregistered chats are blocked,
    except the admin's private chat, which always acts as an assistant so the
    admin can operate the bot before any chat is registered.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)

        settings: Settings = data["settings"]
        role = await _resolve_role(event.chat.id)

        if role is None:
            user_id = event.from_user.id if event.from_user else None
            if is_private_chat(event) and await is_admin(user_id, settings):
                role = "assistant"
            elif is_private_chat(event):
                await event.answer("Чат не настроен. Обратитесь к администратору.")
                return None
            else:
                return None

        data["chat_role"] = role
        return await handler(event, data)
