import logging
from dataclasses import dataclass

from noteturner.integrations.openrouter import OpenRouterClient, OpenRouterError
from noteturner.services.llm.prompts import PromptBuilder
from noteturner.services.llm.retriever import ContextRetriever, NullRetriever, SourceChunk
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

    async def answer(self, question: str, *, is_admin: bool) -> AnswerResult:
        tier = self._router.classify(question)
        context = await self._retriever.retrieve(question, include_financial=is_admin)
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
