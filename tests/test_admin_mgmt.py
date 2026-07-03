from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest
from noteturner.bot.handlers.admin import (
    _answer_callback,
    _do_remove_admin,
    _format_drive_discovery,
    _format_sync_health,
)
from noteturner.bot.keyboards.admin import admin_menu
from noteturner.bot.user_resolve import parse_telegram_id
from noteturner.config.settings import Settings
from noteturner.integrations.gdrive import DriveFile, DriveListResult, DriveRootSummary


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


def test_format_drive_discovery_with_supported_files() -> None:
    discovery = DriveListResult(
        files=[
            DriveFile(id="1", name="Doc 1", mime_type="application/vnd.google-apps.document"),
            DriveFile(id="2", name="Sheet 1", mime_type="application/vnd.google-apps.spreadsheet"),
        ],
        roots=[DriveRootSummary(root_id="root", name="Virtuozy", mime_type="application/vnd.google-apps.folder")],
        skipped_by_mime={},
    )

    lines = _format_drive_discovery(discovery)

    assert "Google Drive (Virtuozy; 2 файлов)" in lines[0]
    assert "doc: 1" in lines[1]
    assert "sheet: 1" in lines[1]


def test_format_drive_discovery_when_empty_includes_hint() -> None:
    discovery = DriveListResult(
        files=[],
        roots=[
            DriveRootSummary(
                root_id="root",
                name="Empty Root",
                mime_type="application/vnd.google-apps.folder",
                items_seen=0,
            )
        ],
        skipped_by_mime={},
    )

    lines = _format_drive_discovery(discovery)

    assert "Google Drive (Empty Root; 0 файлов)" in lines[0]
    assert any("Файлы не найдены" in line for line in lines[1:])


def test_admin_menu_includes_check_sources_button() -> None:
    markup = admin_menu()
    callback_data = {
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data is not None
    }

    assert "admin:check_sources" in callback_data


async def test_answer_callback_ignores_expired_query() -> None:
    query = AsyncMock()
    query.data = "admin:stats"
    query.answer = AsyncMock(
        side_effect=TelegramBadRequest(
            method="answerCallbackQuery",
            message="query is too old and response timeout expired or query ID is invalid",
        )
    )

    result = await _answer_callback(query, "ok")

    assert result is False
