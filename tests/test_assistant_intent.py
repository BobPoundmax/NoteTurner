from noteturner.bot.handlers.assistant import (
    detect_crm_refresh_scope,
    is_data_coverage_intent,
    is_sync_intent,
    is_sync_status_intent,
)


def test_sync_intent_matches_collection_requests() -> None:
    assert is_sync_intent("Можешь запустить сбор данных?") is True
    assert is_sync_intent("обнови CRM пожалуйста") is True
    assert is_sync_intent("Синхронизируй источники") is True


def test_sync_intent_ignores_regular_questions() -> None:
    assert is_sync_intent("Какая выручка в марте?") is False
    assert is_sync_intent("Сколько у нас учеников?") is False


def test_sync_status_intent_matches_status_questions() -> None:
    assert is_sync_status_intent("Ты закончил выгрузку данных из холихоп?") is True
    assert is_sync_status_intent("Как там синхронизация CRM?") is True


def test_sync_status_intent_ignores_regular_questions() -> None:
    assert is_sync_status_intent("Обнови CRM пожалуйста") is False
    assert is_sync_status_intent("Сколько у нас учеников?") is False


def test_data_coverage_intent_matches_admin_stats_questions() -> None:
    assert is_data_coverage_intent("Сколько у тебя сейчас данных в векторной базе?") is True
    assert is_data_coverage_intent("Какие данные по лидам и расписанию уже загружены?") is True


def test_data_coverage_intent_ignores_regular_questions() -> None:
    assert is_data_coverage_intent("Какие завтра стоят уроки в расписании?") is False
    assert is_data_coverage_intent("Обнови CRM пожалуйста") is False


def test_detect_crm_refresh_scope() -> None:
    assert detect_crm_refresh_scope("обнови платежи за сегодня")[0] == "finance"
    assert detect_crm_refresh_scope("обнови студентов")[0] == "students"
    assert detect_crm_refresh_scope("синхронизируй группы")[0] == "groups"
    assert detect_crm_refresh_scope("обнови лиды и заявки")[0] == "leads"
    assert detect_crm_refresh_scope("загрузи google drive") is None
