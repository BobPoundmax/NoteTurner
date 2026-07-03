from datetime import datetime, timezone
from typing import Any

from aiogram import Bot

from noteturner.db.session import check_database
from noteturner.integrations.gdrive import GoogleDriveClient
from noteturner.integrations.hollihop import HollihopClient
from noteturner.integrations.openrouter import OpenRouterClient


def _shallow_dependency_status(note: str) -> dict[str, Any]:
    return {"status": "ok", "mode": "shallow", "note": note}


async def run_health_checks(
    *,
    bot: Bot | None,
    openrouter: OpenRouterClient,
    hollihop: HollihopClient,
    gdrive: GoogleDriveClient,
    deep: bool = False,
) -> dict[str, Any]:
    checks: dict[str, dict] = {}

    checks["database"] = await check_database()

    if deep and openrouter.is_configured:
        checks["openrouter"] = await openrouter.health_check()
    elif openrouter.is_configured:
        checks["openrouter"] = _shallow_dependency_status("OpenRouter probe skipped")
    else:
        checks["openrouter"] = {"status": "skipped", "error": "OPENROUTER_API_KEY not set"}

    if deep and hollihop.is_configured:
        checks["hollihop"] = await hollihop.health_check()
    elif hollihop.is_configured:
        checks["hollihop"] = _shallow_dependency_status("Hollihop probe skipped")
    else:
        checks["hollihop"] = {
            "status": "skipped",
            "error": "HOLLIHOP_SUBDOMAIN / HOLLIHOP_AUTH_KEY not set",
        }

    if deep:
        checks["gdrive"] = await gdrive.health_check()
    elif gdrive.is_configured:
        checks["gdrive"] = _shallow_dependency_status("Google Drive probe skipped")
    else:
        checks["gdrive"] = {
            "status": "skipped",
            "error": "GDRIVE_FOLDER_ID or Google service account env vars not set",
        }

    if deep and bot is not None:
        try:
            me = await bot.get_me()
            checks["telegram"] = {"status": "ok", "username": me.username}
        except Exception as exc:
            checks["telegram"] = {"status": "error", "error": str(exc)}
    elif bot is not None:
        checks["telegram"] = _shallow_dependency_status("Telegram probe skipped")
    else:
        checks["telegram"] = {"status": "skipped", "error": "Bot not initialized"}

    failed = [name for name, result in checks.items() if result.get("status") == "error"]
    critical_failed = [name for name in failed if name == "database"]
    overall = "degraded" if failed else "ok"

    return {
        "status": overall,
        "critical_ok": not critical_failed,
        "mode": "deep" if deep else "shallow",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }
