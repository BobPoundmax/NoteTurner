import logging
from dataclasses import dataclass

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
    def _no_context_reply(question: str) -> str | None:
        preferences = classify_query_preferences(question)
        if not preferences.requires_corporate_context:
            return None
        return (
            "Не нашёл релевантных данных в локальном индексе CRM/Drive. "
            "Обновите источники через /admin или попросите обновить нужный "
            "раздел CRM командой вроде «обнови платежи» или «обнови группы»."
        )

    async def answer(self, question: str, *, is_admin: bool) -> AnswerResult:
        tier = self._router.classify(question)
        context = await self._retriever.retrieve(question, include_financial=is_admin)
        if is_admin and not context:
            no_context_reply = self._no_context_reply(question)
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
                last_error = exc
                continue

            if context:
                reply = f"{reply}\n\n{self._format_sources(context)}"
            return AnswerResult(text=reply, model=model, tier=tier)

        raise OpenRouterError(
            f"Все модели уровня '{tier}' недоступны"
            + (f": {last_error}" if last_error else "")
        )
