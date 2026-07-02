from noteturner.config.loader import load_yaml

SIMPLE = "simple"
COMPLEX = "complex"


class ModelRouter:
    """Classify a question into a tier and pick models (tier + global fallback)."""

    def __init__(self, config: dict | None = None) -> None:
        cfg = config if config is not None else load_yaml("routing.yaml")
        routing = cfg.get("routing", {})
        self._simple = routing.get("simple", {})
        self._complex = routing.get("complex", {})
        self._fallback: str | None = routing.get("fallback")

    @property
    def max_chars(self) -> int:
        return int(self._simple.get("max_chars", 200))

    def _keywords(self, tier: dict) -> list[str]:
        return [k.lower() for k in tier.get("keywords", [])]

    def classify(self, question: str) -> str:
        text = (question or "").lower()
        if any(kw in text for kw in self._keywords(self._complex)):
            return COMPLEX
        if any(kw in text for kw in self._keywords(self._simple)):
            return SIMPLE
        if len(question or "") > self.max_chars:
            return COMPLEX
        return SIMPLE

    def models_for(self, tier: str) -> list[str]:
        source = self._complex if tier == COMPLEX else self._simple
        models: list[str] = list(source.get("models", []))
        if self._fallback and self._fallback not in models:
            models.append(self._fallback)
        return models
