from unittest.mock import AsyncMock, MagicMock

from aiogram.enums import ChatType
from aiogram.types import Message

from noteturner.bot.middlewares.chat_access import ChatAccessMiddleware
from noteturner.config.settings import Settings


def _make_message(chat_type: ChatType, *, chat_id: int = 123, user_id: int = 1) -> MagicMock:
    message = MagicMock(spec=Message)
    message.chat = MagicMock()
    message.chat.id = chat_id
    message.chat.type = chat_type
    message.from_user = MagicMock()
    message.from_user.id = user_id
    message.answer = AsyncMock()
    return message


async def test_admin_private_unregistered_acts_as_assistant() -> None:
    settings = Settings(admin_telegram_id=1)
    middleware = ChatAccessMiddleware()
    handler = AsyncMock()
    message = _make_message(ChatType.PRIVATE, user_id=1)
    data: dict = {"settings": settings}

    await middleware(handler, message, data)

    handler.assert_awaited_once()
    assert data["chat_role"] == "assistant"


async def test_non_admin_private_unregistered_is_blocked() -> None:
    settings = Settings(admin_telegram_id=1)
    middleware = ChatAccessMiddleware()
    handler = AsyncMock()
    message = _make_message(ChatType.PRIVATE, user_id=999)
    data: dict = {"settings": settings}

    result = await middleware(handler, message, data)

    handler.assert_not_awaited()
    message.answer.assert_awaited_once()
    assert result is None


async def test_unregistered_group_is_silent() -> None:
    settings = Settings(admin_telegram_id=1)
    middleware = ChatAccessMiddleware()
    handler = AsyncMock()
    message = _make_message(ChatType.SUPERGROUP, user_id=1)
    data: dict = {"settings": settings}

    result = await middleware(handler, message, data)

    handler.assert_not_awaited()
    message.answer.assert_not_awaited()
    assert result is None
