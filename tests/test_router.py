from noteturner.config.loader import load_yaml
from noteturner.services.llm.router import COMPLEX, SIMPLE, ModelRouter

CFG = {
    "routing": {
        "simple": {"models": ["m-simple"], "max_chars": 20, "keywords": ["привет"]},
        "complex": {"models": ["m-complex"], "keywords": ["проанализируй"]},
        "fallback": "m-fallback",
    }
}


def test_classify_complex_keyword() -> None:
    assert ModelRouter(CFG).classify("Проанализируй продажи") == COMPLEX


def test_classify_simple_keyword() -> None:
    assert ModelRouter(CFG).classify("Привет") == SIMPLE


def test_classify_long_question_is_complex() -> None:
    assert ModelRouter(CFG).classify("я" * 50) == COMPLEX


def test_classify_short_question_defaults_simple() -> None:
    assert ModelRouter(CFG).classify("как дела") == SIMPLE


def test_models_for_appends_fallback() -> None:
    router = ModelRouter(CFG)
    assert router.models_for(COMPLEX) == ["m-complex", "m-fallback"]
    assert router.models_for(SIMPLE) == ["m-simple", "m-fallback"]


def test_models_for_no_duplicate_fallback() -> None:
    cfg = {
        "routing": {
            "simple": {"models": ["m-fallback"]},
            "complex": {"models": []},
            "fallback": "m-fallback",
        }
    }
    assert ModelRouter(cfg).models_for(SIMPLE) == ["m-fallback"]


def test_config_files_load() -> None:
    assert "routing" in load_yaml("routing.yaml")
    assert "assistant" in load_yaml("prompts.yaml")
