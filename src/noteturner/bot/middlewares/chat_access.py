import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from noteturner.bot.utils import is_private_chat
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

    Registered chats get ``chat_role`` injected. Any private chat acts as an
    assistant by default, while unregistered group chats stay silent.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)

        role = await _resolve_role(event.chat.id)

        if role is None:
            if is_private_chat(event):
                role = "assistant"
            else:
                return None

        data["chat_role"] = role
        return await handler(event, data)
