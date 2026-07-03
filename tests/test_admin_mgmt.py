from noteturner.bot.handlers.admin import _do_remove_admin, _format_sync_health
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


def test_format_sync_health_for_successful_sync() -> None:
    message = _format_sync_health(
        "CRM",
        {
            "sync_runs": {
                "hollihop": {
                    "last_run": {
                        "status": "ok",
                        "started_at": "2026-07-03T20:00:00+00:00",
                        "finished_at": "2026-07-03T20:05:00+00:00",
                    },
                    "last_success_at": "2026-07-03T20:05:00+00:00",
                }
            }
        },
        "hollihop",
    )

    assert "последняя успешная синхронизация" in message


def test_format_sync_health_when_no_syncs_yet() -> None:
    message = _format_sync_health("Google Drive", {"sync_runs": {}}, "gdrive")

    assert "синхронизаций ещё не было" in message
