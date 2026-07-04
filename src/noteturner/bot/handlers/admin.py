import asyncio
import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from noteturner.bot.access import is_admin, is_main_admin
from noteturner.bot.keyboards.admin import admin_menu, admins_menu, role_choice
from noteturner.bot.user_resolve import (
    format_user_label,
    parse_telegram_id,
    resolve_admin_target,
    resolve_telegram_user,
)
from noteturner.config.settings import Settings
from noteturner.db.repositories import admins as admins_repo
from noteturner.db.repositories.chats import count_chats_by_role, upsert_chat
from noteturner.db.repositories.collector import count_collector_messages
from noteturner.db.repositories.query_logs import count_query_logs
from noteturner.db.repositories.sync import count_raw_records, count_raw_records_by_type, recent_sync_runs
from noteturner.db.repositories.vectors import count_doc_chunks_by_record_type, count_doc_chunks_by_source
from noteturner.db.session import check_database, session_scope
from noteturner.integrations.gdrive import DriveListResult, GoogleDriveClient
from noteturner.integrations.hollihop import HollihopClient
from noteturner.integrations.openrouter import OpenRouterClient
from noteturner.services.sync_jobs import (
    ensure_drive_sync_job,
    ensure_hollihop_sync_job,
    format_running_drive_sync_message,
    format_running_sync_message,
)
from noteturner.services.crm_sync import get_scope_record_types

router = Router()
logger = logging.getLogger(__name__)
CRM_SCOPE_LABELS: dict[str, str] = {
    "all": "данных CRM",
    "students": "студентов",
    "finance": "платежей и балансов",
    "leads": "лидов и заявок",
    "groups": "групп и расписания",
}


class AddChatStates(StatesGroup):
    waiting_for_chat_id = State()
    waiting_for_role = State()


class AdminMgmtStates(StatesGroup):
    waiting_for_add_id = State()
    waiting_for_del_id = State()


def _format_check(name: str, result: dict) -> str:
    status = result.get("status", "unknown")
    if status == "ok":
        extra = ""
        if "latency_ms" in result:
            extra = f" ({result['latency_ms']} ms)"
        if "locations_count" in result:
            extra = f" ({result['locations_count']} локаций)"
        if "folder_name" in result:
            extra = f" ({result['folder_name']})"
        if "files_count" in result:
            extra = f" ({result['files_count']} файлов)"
        return f"✅ {name}{extra}"
    if status == "skipped":
        return f"⏭ {name}: {result.get('error', 'not configured')}"
    return f"❌ {name}: {result.get('error', 'unknown error')}"


def _format_health_timestamp(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%d.%m %H:%M")
    except ValueError:
        return value


def _format_sync_health(name: str, database_result: dict, source: str) -> str:
    sync_runs = database_result.get("sync_runs") or {}
    sync_info = sync_runs.get(source) or {}
    last_run = sync_info.get("last_run") or {}
    last_success_at = _format_health_timestamp(sync_info.get("last_success_at"))
    last_status = last_run.get("status")
    last_finished_at = _format_health_timestamp(last_run.get("finished_at"))
    last_started_at = _format_health_timestamp(last_run.get("started_at"))

    if not last_run:
        return f"• {name}: синхронизаций ещё не было"
    if last_status == "running":
        success_part = last_success_at or "ещё не было"
        return (
            f"• {name}: идёт синхронизация (старт {last_started_at or 'неизвестно'}), "
            f"последняя успешная {success_part}"
        )
    if last_success_at:
        return f"• {name}: последняя успешная синхронизация {last_success_at}"
    finished_part = last_finished_at or last_started_at or "неизвестно"
    return f"• {name}: последняя синхронизация завершилась со статусом {last_status} ({finished_part})"


def _format_drive_discovery(discovery: DriveListResult) -> list[str]:
    roots = [root.name for root in discovery.roots if root.name]
    if roots:
        roots_label = ", ".join(roots[:2])
        if len(roots) > 2:
            roots_label += f" +{len(roots) - 2}"
    else:
        roots_label = "корни не определены"

    files_count = len(discovery.files)
    per_type: dict[str, int] = {}
    for file in discovery.files:
        per_type[file.record_type] = per_type.get(file.record_type, 0) + 1

    lines = [f"✅ Google Drive ({roots_label}; {files_count} файлов)"]
    if per_type:
        type_summary = ", ".join(
            f"{record_type}: {count}" for record_type, count in sorted(per_type.items())
        )
        lines.append(f"• Поддерживаемые типы: {type_summary}")
    if files_count == 0 and discovery.hint_when_empty:
        lines.append(f"⚠️ {discovery.hint_when_empty}")
    return lines


async def _answer_callback(
    query: CallbackQuery,
    text: str | None = None,
    *,
    show_alert: bool = False,
) -> bool:
    try:
        await query.answer(text, show_alert=show_alert)
        return True
    except TelegramBadRequest as exc:
        error_text = str(exc).lower()
        if "query is too old" in error_text or "query id is invalid" in error_text:
            logger.info("Ignoring expired callback query: %s", query.data)
            return False
        raise


async def _render_sources_status(
    hollihop: HollihopClient,
    gdrive: GoogleDriveClient,
) -> str:
    hollihop_task = (
        hollihop.health_check()
        if hollihop.is_configured
        else {"status": "skipped", "error": "HOLLIHOP_SUBDOMAIN / HOLLIHOP_AUTH_KEY not set"}
    )

    # Use the lightweight health probe instead of a full recursive Drive listing:
    # rendering source status should not walk every folder/file (memory + latency).
    drive_task = (
        gdrive.health_check()
        if gdrive.is_configured
        else {"status": "skipped", "error": "GDRIVE_FOLDER_ID or Google service account env vars not set"}
    )

    hh_result, gd_result = await asyncio.gather(
        hollihop_task if asyncio.iscoroutine(hollihop_task) else asyncio.sleep(0, result=hollihop_task),
        drive_task if asyncio.iscoroutine(drive_task) else asyncio.sleep(0, result=drive_task),
        return_exceptions=True,
    )

    lines = [
        "<b>Проверка источников</b>",
        "",
        "Живой запрос к внешним источникам данных:",
        _format_check("Hollihop CRM", hh_result)
        if not isinstance(hh_result, Exception)
        else f"❌ Hollihop CRM: {hh_result}",
    ]

    if isinstance(gd_result, Exception):
        logger.exception("Google Drive live check failed")
        lines.append(f"❌ Google Drive: {gd_result}")
    elif isinstance(gd_result, dict):
        lines.append(_format_check("Google Drive", gd_result))
    else:
        lines.extend(_format_drive_discovery(gd_result))

    return "\n".join(lines)


async def _start_crm_sync(
    query: CallbackQuery,
    settings: Settings,
    hollihop: HollihopClient,
    openrouter: OpenRouterClient,
    *,
    scope: str,
) -> None:
    if not await is_admin(query.from_user.id, settings):
        await _answer_callback(query, "Только для администратора.", show_alert=True)
        return
    label = CRM_SCOPE_LABELS.get(scope, "данных CRM")
    await _answer_callback(query, f"Запускаю выгрузку {label}…")
    started, job = await ensure_hollihop_sync_job(
        query.bot,
        query.message.chat.id,
        hollihop,
        openrouter,
        label=label,
        record_types=None if scope == "all" else get_scope_record_types(scope),
    )
    if not started:
        await query.message.answer(format_running_sync_message(job))


def _parse_telegram_id(raw: str | None) -> int | None:
    return parse_telegram_id(raw)


async def _do_add_admin(
    telegram_id: int,
    added_by: int | None,
    *,
    username: str | None = None,
) -> str:
    async with session_scope() as session:
        created = await admins_repo.add_admin(
            session, telegram_id=telegram_id, added_by=added_by
        )
    label = format_user_label(telegram_id=telegram_id, username=username)
    if created:
        return f"✅ Админ {label} добавлен."
    return f"ℹ️ {label} уже админ."


async def _do_remove_admin(
    telegram_id: int,
    settings: Settings,
    *,
    username: str | None = None,
) -> str:
    if is_main_admin(telegram_id, settings):
        return "⛔ Главного админа (из env) удалить нельзя."
    async with session_scope() as session:
        removed = await admins_repo.remove_admin(session, telegram_id=telegram_id)
    label = format_user_label(telegram_id=telegram_id, username=username)
    if removed:
        return f"✅ Админ {label} удалён."
    return f"ℹ️ {label} не был админом."


async def _render_admins(settings: Settings) -> str:
    lines = ["<b>Администраторы</b>", "", f"👑 {settings.admin_telegram_id} — главный (из env, неизменяем)"]
    try:
        async with session_scope() as session:
            admins = await admins_repo.list_admins(session)
    except RuntimeError:
        lines.append("")
        lines.append("База данных не настроена.")
        return "\n".join(lines)

    if admins:
        for admin in admins:
            added = f" (добавил {admin.added_by})" if admin.added_by else ""
            lines.append(f"• {admin.telegram_id}{added}")
    else:
        lines.append("• дополнительных админов нет")
    lines.append("")
    lines.append(
        "Добавить: <code>/addadmin @username</code> или <code>/addadmin telegram_id</code>"
    )
    return "\n".join(lines)


@router.message(Command("status"))
async def cmd_status(
    message: Message,
    settings: Settings,
    openrouter: OpenRouterClient,
    hollihop: HollihopClient,
    gdrive: GoogleDriveClient,
) -> None:
    user_id = message.from_user.id if message.from_user else None
    if not await is_admin(user_id, settings):
        await message.answer("Команда /status доступна только администратору.")
        return

    db_result = await check_database()
    or_result = await openrouter.health_check()
    hh_result = (
        await hollihop.health_check()
        if hollihop.is_configured
        else {"status": "skipped", "error": "HOLLIHOP_SUBDOMAIN / HOLLIHOP_AUTH_KEY not set"}
    )
    gd_result = await gdrive.health_check()

    bot_info = await message.bot.get_me()
    lines = [
        "<b>Статус Note Turner</b>",
        "",
        _format_check("Database", db_result),
        _format_check("OpenRouter", or_result),
        _format_check("Hollihop CRM", hh_result),
        _format_check("Google Drive", gd_result),
    ]
    if db_result.get("status") == "ok":
        lines.extend(
            [
                "",
                "<b>Последние обновления данных</b>",
                _format_sync_health("CRM", db_result, "hollihop"),
                _format_sync_health("Google Drive", db_result, "gdrive"),
            ]
        )
    lines.extend(
        [
            "",
            f"Bot: @{bot_info.username}",
            f"Mode: {settings.bot_mode}",
        ]
    )
    await message.answer("\n".join(lines))


@router.message(Command("sources"))
async def cmd_sources(
    message: Message,
    settings: Settings,
    hollihop: HollihopClient,
    gdrive: GoogleDriveClient,
) -> None:
    user_id = message.from_user.id if message.from_user else None
    if not await is_admin(user_id, settings):
        await message.answer("Команда /sources доступна только администратору.")
        return

    await message.answer(await _render_sources_status(hollihop, gdrive))


@router.message(Command("admin"))
async def cmd_admin(message: Message, settings: Settings) -> None:
    user_id = message.from_user.id if message.from_user else None
    if not await is_admin(user_id, settings):
        await message.answer("Меню /admin доступно только администратору.")
        return
    await message.answer("<b>Панель администратора</b>", reply_markup=admin_menu())


@router.message(Command("admins"))
async def cmd_admins(message: Message, settings: Settings) -> None:
    user_id = message.from_user.id if message.from_user else None
    if not await is_admin(user_id, settings):
        await message.answer("Команда доступна только администратору.")
        return
    await message.answer(await _render_admins(settings))


@router.message(Command("addadmin"))
async def cmd_add_admin(message: Message, settings: Settings, command: CommandObject) -> None:
    user_id = message.from_user.id if message.from_user else None
    if not await is_admin(user_id, settings):
        await message.answer("Команда доступна только администратору.")
        return
    if not command.args:
        await message.answer(
            "Использование: <code>/addadmin @username</code> или "
            "<code>/addadmin telegram_id</code>"
        )
        return
    telegram_id, username, error = await resolve_telegram_user(message.bot, command.args)
    if error:
        await message.answer(error)
        return
    await message.answer(await _do_add_admin(telegram_id, user_id, username=username))


@router.message(Command("deladmin"))
async def cmd_del_admin(message: Message, settings: Settings, command: CommandObject) -> None:
    user_id = message.from_user.id if message.from_user else None
    if not await is_admin(user_id, settings):
        await message.answer("Команда доступна только администратору.")
        return
    if not command.args:
        await message.answer(
            "Использование: <code>/deladmin @username</code> или "
            "<code>/deladmin telegram_id</code>"
        )
        return
    telegram_id, username, error = await resolve_telegram_user(message.bot, command.args)
    if error:
        await message.answer(error)
        return
    await message.answer(await _do_remove_admin(telegram_id, settings, username=username))


@router.callback_query(F.data == "admin:add_chat")
async def cb_add_chat(query: CallbackQuery, settings: Settings, state: FSMContext) -> None:
    if not await is_admin(query.from_user.id, settings):
        await _answer_callback(query, "Только для администратора.", show_alert=True)
        return
    await state.set_state(AddChatStates.waiting_for_chat_id)
    await query.message.answer(
        "Отправьте <b>chat_id</b> чата (число, например <code>-1001234567890</code>)."
    )
    await _answer_callback(query)


@router.callback_query(F.data == "admin:check_sources")
async def cb_check_sources(
    query: CallbackQuery,
    settings: Settings,
    hollihop: HollihopClient,
    gdrive: GoogleDriveClient,
) -> None:
    if not await is_admin(query.from_user.id, settings):
        await _answer_callback(query, "Только для администратора.", show_alert=True)
        return
    await _answer_callback(query, "Проверяю источники…")
    await query.message.answer(await _render_sources_status(hollihop, gdrive))


@router.message(StateFilter(AddChatStates.waiting_for_chat_id), F.text)
async def add_chat_id(message: Message, settings: Settings, state: FSMContext) -> None:
    if not await is_admin(message.from_user.id if message.from_user else None, settings):
        return
    chat_id = _parse_telegram_id(message.text)
    if chat_id is None:
        await message.answer("Некорректный chat_id. Отправьте целое число.")
        return
    await state.update_data(chat_id=chat_id)
    await state.set_state(AddChatStates.waiting_for_role)
    await message.answer("Выберите роль чата:", reply_markup=role_choice())


@router.callback_query(
    StateFilter(AddChatStates.waiting_for_role), F.data.startswith("admin:role:")
)
async def add_chat_role(query: CallbackQuery, settings: Settings, state: FSMContext) -> None:
    if not await is_admin(query.from_user.id, settings):
        await _answer_callback(query, "Только для администратора.", show_alert=True)
        return
    role = query.data.split(":")[-1]
    data = await state.get_data()
    chat_id = data.get("chat_id")
    await state.clear()

    if chat_id is None:
        await query.message.answer("Не удалось определить chat_id. Начните заново: /admin")
        await _answer_callback(query)
        return

    async with session_scope() as session:
        await upsert_chat(session, telegram_chat_id=chat_id, role=role)

    await query.message.answer(f"Чат <code>{chat_id}</code> сохранён с ролью <b>{role}</b>.")
    await _answer_callback(query)


@router.callback_query(F.data == "admin:admins")
async def cb_admins(query: CallbackQuery, settings: Settings) -> None:
    if not await is_admin(query.from_user.id, settings):
        await _answer_callback(query, "Только для администратора.", show_alert=True)
        return
    await query.message.answer(await _render_admins(settings), reply_markup=admins_menu())
    await _answer_callback(query)


@router.callback_query(F.data == "admin:admin_add")
async def cb_admin_add(query: CallbackQuery, settings: Settings, state: FSMContext) -> None:
    if not await is_admin(query.from_user.id, settings):
        await _answer_callback(query, "Только для администратора.", show_alert=True)
        return
    await state.set_state(AdminMgmtStates.waiting_for_add_id)
    await query.message.answer(
        "Отправьте <b>telegram_id</b>, <b>@username</b> "
        "или перешлите сообщение нового админа."
    )
    await _answer_callback(query)


@router.message(StateFilter(AdminMgmtStates.waiting_for_add_id))
async def admin_add_id(message: Message, settings: Settings, state: FSMContext) -> None:
    if not await is_admin(message.from_user.id if message.from_user else None, settings):
        return
    await state.clear()
    telegram_id, username, error = await resolve_admin_target(message)
    if error:
        await message.answer(error)
        return
    added_by = message.from_user.id if message.from_user else None
    await message.answer(await _do_add_admin(telegram_id, added_by, username=username))


@router.callback_query(F.data == "admin:admin_del")
async def cb_admin_del(query: CallbackQuery, settings: Settings, state: FSMContext) -> None:
    if not await is_admin(query.from_user.id, settings):
        await _answer_callback(query, "Только для администратора.", show_alert=True)
        return
    await state.set_state(AdminMgmtStates.waiting_for_del_id)
    await query.message.answer(
        "Отправьте <b>telegram_id</b> или <b>@username</b> админа для удаления."
    )
    await _answer_callback(query)


@router.message(StateFilter(AdminMgmtStates.waiting_for_del_id), F.text)
async def admin_del_id(message: Message, settings: Settings, state: FSMContext) -> None:
    if not await is_admin(message.from_user.id if message.from_user else None, settings):
        return
    await state.clear()
    telegram_id, username, error = await resolve_telegram_user(message.bot, message.text or "")
    if error:
        await message.answer(error)
        return
    await message.answer(await _do_remove_admin(telegram_id, settings, username=username))


@router.callback_query(F.data == "admin:admin_list")
async def cb_admin_list(query: CallbackQuery, settings: Settings) -> None:
    if not await is_admin(query.from_user.id, settings):
        await _answer_callback(query, "Только для администратора.", show_alert=True)
        return
    await query.message.answer(await _render_admins(settings))
    await _answer_callback(query)


@router.callback_query(F.data == "admin:cancel")
async def cb_cancel(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _answer_callback(query)
    await query.message.answer("Отменено.")


@router.callback_query(F.data == "admin:sync_crm")
async def cb_sync_crm(
    query: CallbackQuery,
    settings: Settings,
    hollihop: HollihopClient,
    openrouter: OpenRouterClient,
) -> None:
    await _start_crm_sync(
        query,
        settings,
        hollihop,
        openrouter,
        scope="all",
    )


@router.callback_query(F.data.startswith("admin:sync_crm:"))
async def cb_sync_crm_scope(
    query: CallbackQuery,
    settings: Settings,
    hollihop: HollihopClient,
    openrouter: OpenRouterClient,
) -> None:
    scope = query.data.split(":")[-1]
    if scope not in CRM_SCOPE_LABELS:
        await _answer_callback(query, "Неизвестный тип выгрузки.", show_alert=True)
        return
    await _start_crm_sync(
        query,
        settings,
        hollihop,
        openrouter,
        scope=scope,
    )


@router.callback_query(F.data == "admin:sync_drive")
async def cb_sync_drive(
    query: CallbackQuery,
    settings: Settings,
    gdrive: GoogleDriveClient,
    openrouter: OpenRouterClient,
) -> None:
    if not await is_admin(query.from_user.id, settings):
        await _answer_callback(query, "Только для администратора.", show_alert=True)
        return
    await _answer_callback(query, "Запускаю загрузку Google Drive…")
    started, job = await ensure_drive_sync_job(
        query.bot,
        query.message.chat.id,
        gdrive,
        openrouter,
        settings,
    )
    if not started:
        await query.message.answer(format_running_drive_sync_message(job))


@router.callback_query(F.data == "admin:stats")
async def cb_stats(query: CallbackQuery, settings: Settings) -> None:
    if not await is_admin(query.from_user.id, settings):
        await _answer_callback(query, "Только для администратора.", show_alert=True)
        return
    await _answer_callback(query)

    try:
        async with session_scope() as session:
            roles = await count_chats_by_role(session)
            collector_count = await count_collector_messages(session)
            raw_count = await count_raw_records(session)
            raw_by_type = await count_raw_records_by_type(session, source="hollihop")
            chunks_by_source = await count_doc_chunks_by_source(session)
            chunk_by_type = await count_doc_chunks_by_record_type(session, source="hollihop")
            query_count = await count_query_logs(session)
            runs = await recent_sync_runs(session, limit=3)
    except RuntimeError:
        await query.message.answer("База данных не настроена.")
        return

    lines = [
        "<b>Статистика</b>",
        "",
        f"Чаты: assistant {roles.get('assistant', 0)}, collector {roles.get('collector', 0)}",
        f"Сообщений collector: {collector_count}",
        f"CRM записей (raw): {raw_count}",
        f"Векторных чанков: Drive {chunks_by_source.get('gdrive', 0)}, "
        f"CRM {chunks_by_source.get('hollihop', 0)}",
        f"Запросов к ассистенту: {query_count}",
    ]
    if raw_by_type:
        lines.append("")
        lines.append(
            "CRM по типам: "
            + ", ".join(f"{name} {count}" for name, count in sorted(raw_by_type.items()))
        )
    if chunk_by_type:
        lines.append(
            "CRM чанки: "
            + ", ".join(f"{name} {count}" for name, count in sorted(chunk_by_type.items()))
        )
    if runs:
        lines.append("")
        lines.append("Последние синхронизации:")
        for run in runs:
            lines.append(
                f"• {run.source} — {run.status} ({run.records_processed or 0} зап.)"
            )
    await query.message.answer("\n".join(lines))
