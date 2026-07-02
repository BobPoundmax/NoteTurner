import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from noteturner.config.settings import Settings

logger = logging.getLogger(__name__)

_DEBUG_LOG_PATH = Path(__file__).resolve().parents[3] / "debug-3060d7.log"


def _agent_debug_log(
    *,
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict[str, Any],
    run_id: str = "pre-fix",
) -> None:
    # #region agent log
    try:
        payload = {
            "sessionId": "3060d7",
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with _DEBUG_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
    # #endregion


class OpenRouterError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class OpenRouterClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def is_configured(self) -> bool:
        return bool(self._settings.openrouter_api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self._settings.webhook_base_url or "https://noteturner.local",
            "X-Title": "Note Turner",
        }

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        max_tokens: int = 512,
        extra: dict[str, Any] | None = None,
    ) -> str:
        if not self.is_configured:
            raise OpenRouterError("OpenRouter is not configured (OPENROUTER_API_KEY)")

        resolved_model = model or self._settings.openrouter_test_model
        payload: dict[str, Any] = {
            "model": resolved_model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if extra:
            payload.update(extra)

        url = f"{self._settings.openrouter_base_url.rstrip('/')}/chat/completions"

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, json=payload, headers=self._headers())

        if response.status_code >= 400:
            raise OpenRouterError(
                f"HTTP {response.status_code}: {response.text[:300]}",
                status_code=response.status_code,
            )

        data = response.json()
        choice = data.get("choices", [{}])[0] if data.get("choices") else {}
        message = choice.get("message") or {}
        content = message.get("content")
        # #region agent log
        _agent_debug_log(
            hypothesis_id="H1-H3",
            location="openrouter.py:chat_completion",
            message="OpenRouter completion parsed",
            data={
                "model": resolved_model,
                "max_tokens": max_tokens,
                "status_code": response.status_code,
                "content_is_none": content is None,
                "content_type": type(content).__name__,
                "message_keys": sorted(message.keys()),
                "finish_reason": choice.get("finish_reason"),
                "has_reasoning": "reasoning" in message,
            },
            run_id="post-fix",
        )
        logger.info(
            "OpenRouter completion model=%s content_is_none=%s finish_reason=%s keys=%s",
            resolved_model,
            content is None,
            choice.get("finish_reason"),
            sorted(message.keys()),
        )
        # #endregion
        if content is None or not str(content).strip():
            # #region agent log
            _agent_debug_log(
                hypothesis_id="H1-H2",
                location="openrouter.py:chat_completion",
                message="OpenRouter empty content",
                data={
                    "model": resolved_model,
                    "finish_reason": choice.get("finish_reason"),
                    "message_keys": sorted(message.keys()),
                },
                run_id="post-fix",
            )
            # #endregion
            raise OpenRouterError(
                f"Empty content in response (finish_reason={choice.get('finish_reason')})"
            )
        return str(content).strip()

    async def health_check(self) -> dict[str, Any]:
        started = datetime.now(timezone.utc)
        if not self.is_configured:
            return {"status": "skipped", "error": "OPENROUTER_API_KEY not set"}

        model = self._settings.openrouter_test_model
        extra: dict[str, Any] | None = None
        if "gpt-5" in model:
            extra = {"reasoning": {"effort": "minimal"}}

        try:
            reply = await self.chat_completion(
                [{"role": "user", "content": "Reply with exactly: OK"}],
                max_tokens=128,
                extra=extra,
            )
            # #region agent log
            _agent_debug_log(
                hypothesis_id="H5",
                location="openrouter.py:health_check",
                message="OpenRouter health check succeeded",
                data={"reply_len": len(reply), "model": self._settings.openrouter_test_model},
            )
            # #endregion
            return {
                "status": "ok",
                "reply": reply[:50],
                "model": self._settings.openrouter_test_model,
                "latency_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            }
        except OpenRouterError as exc:
            # #region agent log
            _agent_debug_log(
                hypothesis_id="H4",
                location="openrouter.py:health_check",
                message="OpenRouter health check OpenRouterError",
                data={"error": str(exc)[:200]},
            )
            # #endregion
            return {"status": "error", "error": str(exc)}
        except httpx.HTTPError as exc:
            return {"status": "error", "error": f"Network error: {exc}"}
