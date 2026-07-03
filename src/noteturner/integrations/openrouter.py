import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from noteturner.config.settings import Settings

logger = logging.getLogger(__name__)


def _format_httpx_error(exc: httpx.HTTPError) -> str:
    details = str(exc).strip()
    if details:
        return f"Network error: {exc.__class__.__name__}: {details}"
    return f"Network error: {exc.__class__.__name__}"


class OpenRouterError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _default_extra_for_model(model: str) -> dict[str, Any] | None:
    """gpt-5 models can return empty content without a reasoning hint."""
    if "gpt-5" in model:
        return {"reasoning": {"effort": "minimal"}}
    return None


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
        effective_extra = extra if extra is not None else _default_extra_for_model(resolved_model)
        if effective_extra:
            payload.update(effective_extra)

        url = f"{self._settings.openrouter_base_url.rstrip('/')}/chat/completions"

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(url, json=payload, headers=self._headers())
        except httpx.HTTPError as exc:
            raise OpenRouterError(_format_httpx_error(exc)) from exc

        if response.status_code >= 400:
            raise OpenRouterError(
                f"HTTP {response.status_code}: {response.text[:300]}",
                status_code=response.status_code,
            )

        data = response.json()
        choice = data.get("choices", [{}])[0] if data.get("choices") else {}
        message = choice.get("message") or {}
        content = message.get("content")
        logger.info(
            "OpenRouter completion model=%s content_is_none=%s finish_reason=%s",
            resolved_model,
            content is None,
            choice.get("finish_reason"),
        )
        if content is None or not str(content).strip():
            raise OpenRouterError(
                f"Empty content in response (finish_reason={choice.get('finish_reason')})"
            )
        return str(content).strip()

    async def embed(self, inputs: list[str], *, model: str | None = None) -> list[list[float]]:
        """Return embedding vectors for the given inputs via OpenRouter /embeddings."""
        if not self.is_configured:
            raise OpenRouterError("OpenRouter is not configured (OPENROUTER_API_KEY)")
        if not inputs:
            return []

        resolved_model = model or self._settings.embedding_model
        payload = {"model": resolved_model, "input": inputs}
        url = f"{self._settings.openrouter_base_url.rstrip('/')}/embeddings"

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(url, json=payload, headers=self._headers())
        except httpx.HTTPError as exc:
            raise OpenRouterError(_format_httpx_error(exc)) from exc

        if response.status_code >= 400:
            raise OpenRouterError(
                f"HTTP {response.status_code}: {response.text[:300]}",
                status_code=response.status_code,
            )

        data = response.json()
        items = data.get("data") or []
        if len(items) != len(inputs):
            raise OpenRouterError(
                f"Embedding count mismatch: got {len(items)} for {len(inputs)} inputs"
            )
        ordered = sorted(items, key=lambda item: item.get("index", 0))
        return [item["embedding"] for item in ordered]

    async def health_check(self) -> dict[str, Any]:
        started = datetime.now(timezone.utc)
        if not self.is_configured:
            return {"status": "skipped", "error": "OPENROUTER_API_KEY not set"}

        try:
            reply = await self.chat_completion(
                [{"role": "user", "content": "Reply with exactly: OK"}],
                max_tokens=128,
            )
            return {
                "status": "ok",
                "reply": reply[:50],
                "model": self._settings.openrouter_test_model,
                "latency_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            }
        except OpenRouterError as exc:
            return {"status": "error", "error": str(exc)}
        except httpx.HTTPError as exc:
            return {"status": "error", "error": f"Network error: {exc}"}
