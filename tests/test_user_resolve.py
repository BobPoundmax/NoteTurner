from unittest.mock import AsyncMock, MagicMock

from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest

from noteturner.bot.user_resolve import parse_telegram_id, resolve_telegram_user


def test_parse_telegram_id() -> None:
    assert parse_telegram_id("123") == 123
    assert parse_telegram_id("  -100500 ") == -100500
    assert parse_telegram_id("abc") is None
    assert parse_telegram_id(None) is None


async def test_resolve_telegram_user_by_numeric_id() -> None:
    bot = AsyncMock()
    user_id, username, error = await resolve_telegram_user(bot, "424242")
    assert user_id == 424242
    assert username is None
    assert error is None
    bot.get_chat.assert_not_called()


async def test_resolve_telegram_user_by_username() -> None:
    bot = AsyncMock()
    chat = MagicMock()
    chat.type = ChatType.PRIVATE
    chat.id = 999
    chat.username = "vyacheslav_kub"
    bot.get_chat = AsyncMock(return_value=chat)

    user_id, username, error = await resolve_telegram_user(bot, "@vyacheslav_kub")

    assert user_id == 999
    assert username == "vyacheslav_kub"
    assert error is None
    bot.get_chat.assert_awaited_once_with("@vyacheslav_kub")


async def test_resolve_telegram_user_unknown_username() -> None:
    bot = AsyncMock()
    bot.get_chat = AsyncMock(side_effect=TelegramBadRequest(method="getChat", message="not found"))

    user_id, username, error = await resolve_telegram_user(bot, "swirlingflow")

    assert user_id is None
    assert username == "swirlingflow"
    assert error is not None
    assert "/start" in error
