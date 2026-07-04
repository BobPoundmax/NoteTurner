import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from noteturner.bot.utils import is_private_chat
from noteturner.debug_runtime import agent_debug_log
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
        resolved_role = role

        if role is None:
            if is_private_chat(event):
                role = "assistant"
            else:
                # #region agent log
                agent_debug_log(
                    location="src/noteturner/bot/middlewares/chat_access.py:50",
                    message="Chat rejected by access middleware",
                    data={
                        "chat_id": event.chat.id,
                        "chat_type": event.chat.type,
                        "resolved_role": resolved_role,
                        "effective_role": None,
                        "is_private": False,
                    },
                    hypothesis_id="B",
                    run_id="user-repro",
                )
                # #endregion
                return None

        # #region agent log
        agent_debug_log(
            location="src/noteturner/bot/middlewares/chat_access.py:66",
            message="Chat access resolved",
            data={
                "chat_id": event.chat.id,
                "chat_type": event.chat.type,
                "resolved_role": resolved_role,
                "effective_role": role,
                "is_private": is_private_chat(event),
            },
            hypothesis_id="B",
            run_id="user-repro",
        )
        # #endregion
        data["chat_role"] = role
        return await handler(event, data)
