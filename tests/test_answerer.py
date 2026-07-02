from unittest.mock import AsyncMock

import pytest

from noteturner.integrations.openrouter import OpenRouterError
from noteturner.services.llm.answerer import Answerer
from noteturner.services.llm.prompts import PromptBuilder
from noteturner.services.llm.router import ModelRouter

ROUTING = {
    "routing": {
        "simple": {"models": ["m1", "m2"], "max_chars": 200, "keywords": []},
        "complex": {"models": []},
        "fallback": None,
    }
}
PROMPTS = {"assistant": {"system": "SYS"}}


def _answerer(openrouter: AsyncMock) -> Answerer:
    return Answerer(
        openrouter,
        router=ModelRouter(ROUTING),
        prompt_builder=PromptBuilder(PROMPTS),
    )


async def test_answerer_falls_back_to_second_model() -> None:
    openrouter = AsyncMock()
    openrouter.chat_completion = AsyncMock(side_effect=[OpenRouterError("boom"), "готово"])

    result = await _answerer(openrouter).answer("короткий вопрос", is_admin=False)

    assert result.text == "готово"
    assert result.model == "m2"
    assert openrouter.chat_completion.await_count == 2


async def test_answerer_raises_when_all_models_fail() -> None:
    openrouter = AsyncMock()
    openrouter.chat_completion = AsyncMock(side_effect=OpenRouterError("boom"))

    with pytest.raises(OpenRouterError):
        await _answerer(openrouter).answer("вопрос", is_admin=False)
