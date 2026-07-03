from datetime import datetime, timezone

from aiogram import Bot

from noteturner.db.session import check_database
from noteturner.debug_session import agent_log
from noteturner.integrations.gdrive import GoogleDriveClient
from noteturner.integrations.hollihop import HollihopClient
from noteturner.integrations.openrouter import OpenRouterClient


async def run_health_checks(
    *,
    bot: Bot | None,
    openrouter: OpenRouterClient,
    hollihop: HollihopClient,
    gdrive: GoogleDriveClient,
) -> dict:
    checks: dict[str, dict] = {}

    checks["database"] = await check_database()

    if openrouter.is_configured:
        checks["openrouter"] = await openrouter.health_check()
    else:
        checks["openrouter"] = {"status": "skipped", "error": "OPENROUTER_API_KEY not set"}

    if hollihop.is_configured:
        checks["hollihop"] = await hollihop.health_check()
    else:
        checks["hollihop"] = {
            "status": "skipped",
            "error": "HOLLIHOP_SUBDOMAIN / HOLLIHOP_AUTH_KEY not set",
        }

    # region agent log
    agent_log(
        location="checker.py:run_health_checks",
        message="before gdrive health check",
        data={},
        hypothesis_id="H1",
    )
    # endregion
    checks["gdrive"] = await gdrive.health_check()
    # region agent log
    agent_log(
        location="checker.py:run_health_checks",
        message="after gdrive health check",
        data={"gdrive_status": checks["gdrive"].get("status")},
        hypothesis_id="H1",
    )
    # endregion

    if bot is not None:
        try:
            me = await bot.get_me()
            checks["telegram"] = {"status": "ok", "username": me.username}
        except Exception as exc:
            checks["telegram"] = {"status": "error", "error": str(exc)}
    else:
        checks["telegram"] = {"status": "skipped", "error": "Bot not initialized"}

    failed = [name for name, result in checks.items() if result.get("status") == "error"]
    critical_failed = [
        name
        for name in failed
        if name in {"database", "telegram"}
    ]
    overall = "degraded" if failed else "ok"

    return {
        "status": overall,
        "critical_ok": not critical_failed,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }
