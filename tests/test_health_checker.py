from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import noteturner.health.checker as hc


@pytest.mark.asyncio
async def test_run_health_checks_shallow_skips_external_probes(monkeypatch) -> None:
    monkeypatch.setattr(hc, "check_database", AsyncMock(return_value={"status": "ok"}))

    openrouter = SimpleNamespace(is_configured=True, health_check=AsyncMock())
    hollihop = SimpleNamespace(is_configured=True, health_check=AsyncMock())
    gdrive = SimpleNamespace(is_configured=True, health_check=AsyncMock())
    bot = SimpleNamespace(get_me=AsyncMock())

    result = await hc.run_health_checks(
        bot=bot,
        openrouter=openrouter,
        hollihop=hollihop,
        gdrive=gdrive,
        deep=False,
    )

    assert result["status"] == "ok"
    assert result["mode"] == "shallow"
    assert result["checks"]["openrouter"]["mode"] == "shallow"
    assert result["checks"]["hollihop"]["mode"] == "shallow"
    assert result["checks"]["gdrive"]["mode"] == "shallow"
    assert result["checks"]["telegram"]["mode"] == "shallow"
    openrouter.health_check.assert_not_awaited()
    hollihop.health_check.assert_not_awaited()
    gdrive.health_check.assert_not_awaited()
    bot.get_me.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_health_checks_deep_calls_external_probes(monkeypatch) -> None:
    monkeypatch.setattr(hc, "check_database", AsyncMock(return_value={"status": "ok"}))

    openrouter = SimpleNamespace(
        is_configured=True,
        health_check=AsyncMock(return_value={"status": "ok", "reply": "OK"}),
    )
    hollihop = SimpleNamespace(
        is_configured=True,
        health_check=AsyncMock(return_value={"status": "ok", "locations_count": 3}),
    )
    gdrive = SimpleNamespace(
        is_configured=True,
        health_check=AsyncMock(return_value={"status": "ok", "folder_name": "Root"}),
    )
    bot = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(username="note_turner_bot")))

    result = await hc.run_health_checks(
        bot=bot,
        openrouter=openrouter,
        hollihop=hollihop,
        gdrive=gdrive,
        deep=True,
    )

    assert result["status"] == "ok"
    assert result["mode"] == "deep"
    assert result["checks"]["openrouter"]["reply"] == "OK"
    assert result["checks"]["hollihop"]["locations_count"] == 3
    assert result["checks"]["gdrive"]["folder_name"] == "Root"
    assert result["checks"]["telegram"]["username"] == "note_turner_bot"
    openrouter.health_check.assert_awaited_once()
    hollihop.health_check.assert_awaited_once()
    gdrive.health_check.assert_awaited_once()
    bot.get_me.assert_awaited_once()
