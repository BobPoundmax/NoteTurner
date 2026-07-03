from noteturner.services.llm.prompts import PromptBuilder
from noteturner.services.llm.retriever import SourceChunk

CFG = {"assistant": {"system": "SYS", "disclaimer": "D"}}
CFG_ADMIN = {"assistant": {"system": "SYS", "admin_note": "ADMIN"}}


def test_build_without_context() -> None:
    messages = PromptBuilder(CFG).build("вопрос", [])
    assert messages[0] == {"role": "system", "content": "SYS"}
    assert messages[-1] == {"role": "user", "content": "вопрос"}
    assert len(messages) == 2


def test_build_appends_admin_note_only_for_admin() -> None:
    builder = PromptBuilder(CFG_ADMIN)
    assert not any("ADMIN" in m["content"] for m in builder.build("вопрос", []))
    admin_messages = builder.build("вопрос", [], is_admin=True)
    assert any(m["content"] == "ADMIN" for m in admin_messages)


def test_build_with_context_includes_source() -> None:
    messages = PromptBuilder(CFG).build(
        "вопрос", [SourceChunk(text="данные", source="CRM#1")]
    )
    assert len(messages) == 3
    assert any("CRM#1" in m["content"] for m in messages)
