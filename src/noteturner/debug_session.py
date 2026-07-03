import json
import logging
import time
from pathlib import Path

_LOG_PATH = Path("debug-6edc4c.log")
_SESSION = "6edc4c"
_logger = logging.getLogger("noteturner.debug")


def agent_log(
    *,
    location: str,
    message: str,
    data: dict,
    hypothesis_id: str,
    run_id: str = "pre-fix",
) -> None:
    payload = {
        "sessionId": _SESSION,
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    # region agent log
    try:
        with _LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    _logger.info("DEBUG_SESSION %s", json.dumps(payload, ensure_ascii=False))
    # endregion
