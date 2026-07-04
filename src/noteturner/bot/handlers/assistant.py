import logging

from aiogram import F, Router
from aiogram.types import Message

from noteturner.bot.access import is_admin
from noteturner.bot.filters import ChatRoleFilter
from noteturner.bot.utils import is_bot_mentioned, is_group_chat, strip_bot_mention
from noteturner.config.settings import Settings
from noteturner.debug_runtime import agent_debug_log
from noteturner.db.repositories.sync import recent_sync_runs
from noteturner.db.repositories.query_logs import add_query_log
from noteturner.db.session import session_scope
from noteturner.integrations.hollihop import HollihopClient
from noteturner.integrations.openrouter import OpenRouterClient, OpenRouterError
from noteturner.services.crm_sync import get_scope_record_types
from noteturner.services.data_coverage import build_data_coverage_message
from noteturner.services.llm.answerer import Answerer
from noteturner.services.llm.retriever import VectorRetriever
from noteturner.services.sync_jobs import (
    ensure_hollihop_sync_job,
    format_last_sync_message,
    format_running_sync_message,
    get_running_hollihop_sync_job,
)

logger = logging.getLogger(__name__)

router = Router()

# Phrases signalling that an admin wants to trigger data collection/sync.
# Handled directly (pointing to /admin) instead of guessing via the LLM.
SYNC_INTENT_PHRASES: tuple[str, ...] = (
    "сбор данных",
    "собрать данные",
    "собери данные",
    "запусти сбор",
    "запустить сбор",
    "обнови данные",
    "обновить данные",
    "обнови crm",
    "обновить crm",
    "загрузи crm",
    "выгрузи crm",
    "загрузи google drive",
    "загрузи диск",
    "синхрониз",
)

SYNC_INTENT_REPLY = (
    "Сбор данных запускается вручную из меню <b>/admin</b>:\n"
    "• <b>Загрузить студентов</b> — сначала базовые карточки учеников;\n"
    "• <b>Загрузить платежи</b> — платежи и балансы;\n"
    "• <b>Загрузить лиды</b> — лиды и заявки;\n"
    "• <b>Загрузить группы и расписание</b> — группы, связи и занятия;\n"
    "• <b>Загрузить всё CRM</b> — полный прогон в порядке: студенты, платежи, лиды, остальное;\n"
    "• <b>Загрузить Google Drive</b> — документы и таблицы.\n\n"
    "После загрузки данные векторизуются и я смогу опираться на них в ответах. "
    "Для CRM также доступны точечные команды в личке администратора: "
    "«обнови студентов», «обнови платежи», «обнови лиды», «обнови группы»."
)


def is_sync_intent(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in SYNC_INTENT_PHRASES)


def is_sync_status_intent(text: str) -> bool:
    lowered = text.lower()
    status_markers = ("статус", "закончил", "закончила", "закончилась", "закончено", "как там", "идет", "идёт")
    sync_markers = ("crm", "hollihop", "холихоп", "выгруз", "синхрон", "обновлен", "обновление")
    return any(marker in lowered for marker in status_markers) and any(
        marker in lowered for marker in sync_markers
    )


def is_data_coverage_intent(text: str) -> bool:
    lowered = text.lower()
    count_markers = (
        "сколько",
        "какие данные",
        "что у тебя есть",
        "сколько у тебя",
        "объем",
        "объём",
        "статистика",
        "покрытие",
    )
    data_markers = (
        "вектор",
        "индекс",
        "база",
        "бд",
        "crm",
        "лид",
        "ученик",
        "студент",
        "платеж",
        "баланс",
        "финанс",
        "расписан",
        "урок",
        "групп",
        "заняти",
    )
    return any(marker in lowered for marker in count_markers) and any(
        marker in lowered for marker in data_markers
    )


def detect_crm_refresh_scope(text: str) -> tuple[str, tuple[str, ...], str] | None:
    lowered = text.lower()
    if "google drive" in lowered or "диск" in lowered:
        return None
    if any(keyword in lowered for keyword in ("платеж", "оплат", "баланс", "долг", "задолж")):
        return ("finance", get_scope_record_types("finance"), "платежи и балансы")
    if any(keyword in lowered for keyword in ("студент", "ученик")):
        return ("students", get_scope_record_types("students"), "студентов")
    if any(keyword in lowered for keyword in ("групп", "заняти", "расписан", "урок")):
        return ("groups", get_scope_record_types("groups"), "группы и занятия")
    if any(keyword in lowered for keyword in ("лид", "заявк", "маркет")):
        return ("leads", get_scope_record_types("leads"), "лиды и заявки")
    if any(keyword in lowered for keyword in ("crm", "обнови", "обновить", "синхрониз", "выгрузи")):
        return ("all", get_scope_record_types("all"), "всю CRM")
    return None


async def _log_query(telegram_chat_id: int, question: str, model: str | None) -> None:
    try:
        async with session_scope() as session:
            await add_query_log(
                session,
                telegram_chat_id=telegram_chat_id,
                question=question,
                model=model,
            )
    except RuntimeError:
        pass
    except Exception:
        logger.exception("Failed to log query for chat %s", telegram_chat_id)


async def _reply_with_crm_sync_status(message: Message) -> None:
    running_job = get_running_hollihop_sync_job()
    if running_job is not None:
        await message.answer(format_running_sync_message(running_job))
        return

    try:
        async with session_scope() as session:
            runs = await recent_sync_runs(session, limit=10)
    except RuntimeError:
        await message.answer("Не могу проверить статус CRM-выгрузки: база данных не настроена.")
        return
    except Exception:
        logger.exception("Failed to fetch CRM sync status")
        await message.answer("Не получилось проверить статус CRM-выгрузки. Проверь логи приложения.")
        return

    latest_crm_run = next((run for run in runs if run.source == "hollihop"), None)
    await message.answer(format_last_sync_message(latest_crm_run))


@router.message(F.text, ~F.text.startswith("/"), ChatRoleFilter("assistant"))
async def handle_assistant_message(
    message: Message,
    settings: Settings,
    openrouter: OpenRouterClient,
    hollihop: HollihopClient,
) -> None:
    bot_info = await message.bot.get_me()
    mentioned = is_bot_mentioned(message, bot_info.username)
    if is_group_chat(message) and not mentioned:
        # #region agent log
        agent_debug_log(
            location="src/noteturner/bot/handlers/assistant.py:176",
            message="Assistant ignored group message without mention",
            data={
                "chat_id": message.chat.id,
                "chat_type": message.chat.type,
                "from_user_id": message.from_user.id if message.from_user else None,
                "text_len": len(message.text or ""),
                "mentioned": mentioned,
            },
            hypothesis_id="C",
            run_id="user-repro",
        )
        # #endregion
        return

    user_text = strip_bot_mention(message.text or "", bot_info.username)
    if not user_text:
        await message.answer("Напишите ваш вопрос после упоминания @бота.")
        return

    user_id = message.from_user.id if message.from_user else None
    admin = await is_admin(user_id, settings)
    # #region agent log
    agent_debug_log(
        location="src/noteturner/bot/handlers/assistant.py:200",
        message="Assistant handler accepted message",
        data={
            "chat_id": message.chat.id,
            "chat_type": message.chat.type,
            "from_user_id": user_id,
            "text_len": len(user_text),
            "mentioned": mentioned,
            "is_admin": admin,
        },
        hypothesis_id="C",
        run_id="user-repro",
    )
    # #endregion

    await message.bot.send_chat_action(message.chat.id, "typing")

    if admin and is_sync_status_intent(user_text):
        await _reply_with_crm_sync_status(message)
        return

    if admin and is_data_coverage_intent(user_text):
        try:
            await message.answer(await build_data_coverage_message())
        except RuntimeError:
            await message.answer("Не могу показать покрытие данных: база данных не настроена.")
        return

    if admin and is_sync_intent(user_text):
        scope = detect_crm_refresh_scope(user_text)
        if scope is None:
            await message.answer(SYNC_INTENT_REPLY)
            return
        if not hollihop.is_configured:
            await message.answer(
                "Hollihop CRM не настроен. Администратор должен задать "
                "<code>HOLLIHOP_SUBDOMAIN</code> и <code>HOLLIHOP_AUTH_KEY</code>."
            )
            return

        _, record_types, label = scope
        started, job = await ensure_hollihop_sync_job(
            message.bot,
            message.chat.id,
            hollihop,
            openrouter,
            label=label,
            record_types=record_types,
        )
        if not started:
            await message.answer(format_running_sync_message(job))
            return
        return

    if not openrouter.is_configured:
        await message.answer(
            "OpenRouter не настроен. Администратор должен задать <code>OPENROUTER_API_KEY</code>."
        )
        return

    # doc_chunks may hold Google Drive and/or CRM vectors, so retrieve whenever
    # embeddings are available (OpenRouter is already confirmed configured above).
    retriever = VectorRetriever(openrouter)

    try:
        # #region agent log
        agent_debug_log(
            location="src/noteturner/bot/handlers/assistant.py:258",
            message="Assistant calling answerer",
            data={
                "chat_id": message.chat.id,
                "text_len": len(user_text),
                "is_admin": admin,
            },
            hypothesis_id="D",
            run_id="user-repro",
        )
        # #endregion
        result = await Answerer(openrouter, retriever=retriever).answer(user_text, is_admin=admin)
    except OpenRouterError as exc:
        # #region agent log
        agent_debug_log(
            location="src/noteturner/bot/handlers/assistant.py:271",
            message="Assistant answerer failed with OpenRouterError",
            data={
                "chat_id": message.chat.id,
                "error": str(exc),
            },
            hypothesis_id="D",
            run_id="user-repro",
        )
        # #endregion
        await message.answer(f"Ошибка OpenRouter: {exc}")
        return

    await message.answer(result.text)
    # #region agent log
    agent_debug_log(
        location="src/noteturner/bot/handlers/assistant.py:285",
        message="Assistant sent reply",
        data={
            "chat_id": message.chat.id,
            "model": result.model,
            "tier": result.tier,
            "reply_len": len(result.text or ""),
        },
        hypothesis_id="E",
        run_id="user-repro",
    )
    # #endregion
    await _log_query(message.chat.id, user_text, result.model)
