import logging
from dataclasses import dataclass

from noteturner.debug_runtime import agent_debug_log
from noteturner.integrations.openrouter import OpenRouterClient, OpenRouterError
from noteturner.services.llm.prompts import PromptBuilder
from noteturner.services.llm.retriever import (
    ContextRetriever,
    NullRetriever,
    SourceChunk,
    classify_query_preferences,
)
from noteturner.services.llm.router import ModelRouter

logger = logging.getLogger(__name__)


@dataclass
class AnswerResult:
    text: str
    model: str | None
    tier: str


class Answerer:
    """Orchestrates classification, retrieval, prompt building and model fallback."""

    def __init__(
        self,
        openrouter: OpenRouterClient,
        *,
        router: ModelRouter | None = None,
        prompt_builder: PromptBuilder | None = None,
        retriever: ContextRetriever | None = None,
    ) -> None:
        self._openrouter = openrouter
        self._router = router or ModelRouter()
        self._prompts = prompt_builder or PromptBuilder()
        self._retriever = retriever or NullRetriever()

    def _format_sources(self, context: list[SourceChunk]) -> str:
        seen: list[str] = []
        for chunk in context:
            if chunk.source not in seen:
                seen.append(chunk.source)
        return "📎 Источники: " + ", ".join(seen)

    @staticmethod
    def _no_context_reply(question: str, *, is_admin: bool) -> str | None:
        preferences = classify_query_preferences(question)
        if not preferences.requires_corporate_context:
            return None
        if not is_admin:
            return (
                "Не нашёл релевантных данных в локальном индексе CRM/Drive. "
                "Попросите администратора обновить источники или уточните вопрос."
            )
        return (
            "Не нашёл релевантных данных в локальном индексе CRM/Drive. "
            "Обновите источники через /admin или попросите обновить нужный "
            "раздел CRM командой вроде «обнови платежи» или «обнови группы»."
        )

    @staticmethod
    def _should_attach_sources(reply: str) -> bool:
        normalized = (reply or "").strip().lower()
        negative_prefixes = (
            "не знаю",
            "не наш",
            "не смог",
            "нет данных",
            "недостаточно данных",
        )
        return not normalized.startswith(negative_prefixes)

    async def answer(self, question: str, *, is_admin: bool) -> AnswerResult:
        tier = self._router.classify(question)
        context = await self._retriever.retrieve(question, include_financial=is_admin)
        # #region agent log
        agent_debug_log(
            location="src/noteturner/services/llm/answerer.py:81",
            message="Answerer retrieved context",
            data={
                "question_len": len(question or ""),
                "is_admin": is_admin,
                "tier": tier,
                "context_count": len(context),
                "context_sources": [chunk.source_type or chunk.source for chunk in context[:3]],
                "context_record_types": [chunk.record_type for chunk in context[:3]],
            },
            hypothesis_id="D",
            run_id="user-repro",
        )
        # #endregion
        if not context:
            no_context_reply = self._no_context_reply(question, is_admin=is_admin)
            if no_context_reply is not None:
                return AnswerResult(text=no_context_reply, model=None, tier=tier)
        messages = self._prompts.build(question, context, is_admin=is_admin)

        models = self._router.models_for(tier)
        last_error: OpenRouterError | None = None
        for model in models:
            try:
                reply = await self._openrouter.chat_completion(messages, model=model)
            except OpenRouterError as exc:
                logger.warning("Model %s failed: %s", model, exc)
                # #region agent log
                agent_debug_log(
                    location="src/noteturner/services/llm/answerer.py:106",
                    message="Answerer model failed",
                    data={
                        "tier": tier,
                        "model": model,
                        "error": str(exc),
                    },
                    hypothesis_id="D",
                    run_id="user-repro",
                )
                # #endregion
                last_error = exc
                continue

            if context and self._should_attach_sources(reply):
                reply = f"{reply}\n\n{self._format_sources(context)}"
            # #region agent log
            agent_debug_log(
                location="src/noteturner/services/llm/answerer.py:123",
                message="Answerer produced reply",
                data={
                    "tier": tier,
                    "model": model,
                    "reply_len": len(reply or ""),
                    "attached_sources": bool(context and self._should_attach_sources(reply)),
                },
                hypothesis_id="D",
                run_id="user-repro",
            )
            # #endregion
            return AnswerResult(text=reply, model=model, tier=tier)

        raise OpenRouterError(
            f"Все модели уровня '{tier}' недоступны"
            + (f": {last_error}" if last_error else "")
        )
