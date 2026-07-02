from noteturner.config.loader import load_yaml
from noteturner.services.llm.retriever import SourceChunk


class PromptBuilder:
    def __init__(self, config: dict | None = None) -> None:
        cfg = config if config is not None else load_yaml("prompts.yaml")
        assistant = cfg.get("assistant", {})
        self.system: str = assistant.get(
            "system", "Ты — корпоративный ассистент компании «Виртуозы»."
        ).strip()
        self.disclaimer: str = assistant.get("disclaimer", "").strip()

    def build(self, question: str, context: list[SourceChunk]) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [{"role": "system", "content": self.system}]
        if context:
            blocks = "\n\n".join(
                f"[{i + 1}] Источник: {chunk.source}\n{chunk.text}"
                for i, chunk in enumerate(context)
            )
            messages.append(
                {
                    "role": "system",
                    "content": "Контекст из корпоративных данных:\n\n" + blocks,
                }
            )
        messages.append({"role": "user", "content": question})
        return messages
