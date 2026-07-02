from noteturner.bot.access import is_admin, is_main_admin
from noteturner.config.settings import Settings


def test_is_main_admin() -> None:
    settings = Settings(admin_telegram_id=42)
    assert is_main_admin(42, settings) is True
    assert is_main_admin(7, settings) is False
    assert is_main_admin(None, settings) is False


async def test_is_admin_main_admin_true() -> None:
    settings = Settings(admin_telegram_id=42)
    assert await is_admin(42, settings) is True


async def test_is_admin_non_admin_without_db_is_false() -> None:
    settings = Settings(admin_telegram_id=42)
    assert await is_admin(7, settings) is False


async def test_is_admin_none_is_false() -> None:
    settings = Settings(admin_telegram_id=42)
    assert await is_admin(None, settings) is False
