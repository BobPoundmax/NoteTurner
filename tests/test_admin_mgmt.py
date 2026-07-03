from noteturner.bot.handlers.admin import _do_remove_admin
from noteturner.bot.user_resolve import parse_telegram_id
from noteturner.config.settings import Settings


def test_parse_telegram_id() -> None:
    assert parse_telegram_id("123") == 123
    assert parse_telegram_id("  -100500 ") == -100500
    assert parse_telegram_id("abc") is None
    assert parse_telegram_id(None) is None
    assert parse_telegram_id("") is None


async def test_cannot_remove_main_admin() -> None:
    settings = Settings(admin_telegram_id=42)
    message = await _do_remove_admin(42, settings)
    assert "нельзя" in message.lower()
