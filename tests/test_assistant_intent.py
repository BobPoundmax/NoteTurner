from noteturner.bot.handlers.assistant import detect_crm_refresh_scope, is_sync_intent


def test_sync_intent_matches_collection_requests() -> None:
    assert is_sync_intent("Можешь запустить сбор данных?") is True
    assert is_sync_intent("обнови CRM пожалуйста") is True
    assert is_sync_intent("Синхронизируй источники") is True


def test_sync_intent_ignores_regular_questions() -> None:
    assert is_sync_intent("Какая выручка в марте?") is False
    assert is_sync_intent("Сколько у нас учеников?") is False


def test_detect_crm_refresh_scope() -> None:
    assert detect_crm_refresh_scope("обнови платежи за сегодня")[0] == "finance"
    assert detect_crm_refresh_scope("синхронизируй группы")[0] == "groups"
    assert detect_crm_refresh_scope("обнови лиды и заявки")[0] == "marketing"
    assert detect_crm_refresh_scope("загрузи google drive") is None
