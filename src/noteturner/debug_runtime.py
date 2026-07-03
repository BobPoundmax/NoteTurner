import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEBUG_LOG_PATH = Path("debug-52833e.log")
DEBUG_SESSION_ID = "52833e"


def agent_debug_log(
    *,
    location: str,
    message: str,
    data: dict[str, Any] | None = None,
    hypothesis_id: str,
    run_id: str = "startup-debug",
) -> None:
    payload = {
        "sessionId": DEBUG_SESSION_ID,
        "id": f"{run_id}:{hypothesis_id}:{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
        "location": location,
        "message": message,
        "data": data or {},
        "runId": run_id,
        "hypothesisId": hypothesis_id,
    }
    try:
        with DEBUG_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True) + "\n")
    except OSError:
        pass
    try:
        print(json.dumps(payload, ensure_ascii=True), flush=True)
    except OSError:
        pass
