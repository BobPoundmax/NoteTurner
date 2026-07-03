from aiogram import F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from noteturner.bot.access import is_admin, is_main_admin
from noteturner.bot.keyboards.admin import admin_menu, admins_menu, role_choice
from noteturner.config.settings import Settings
from noteturner.db.repositories import admins as admins_repo
from noteturner.db.repositories.chats import count_chats_by_role, upsert_chat
from noteturner.db.repositories.collector import count_collector_messages
from noteturner.db.repositories.query_logs import count_query_logs
from noteturner.db.repositories.sync import count_raw_records, recent_sync_runs
from noteturner.db.repositories.vectors import count_doc_chunks_by_source
from noteturner.db.session import check_database, session_scope
from noteturner.integrations.gdrive import GoogleDriveClient
from noteturner.integrations.hollihop import HollihopClient
from noteturner.integrations.openrouter import OpenRouterClient
from noteturner.services.crm_sync import run_hollihop_sync
from noteturner.services.drive_sync import run_drive_sync

router = Router()


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


def _parse_telegram_id(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


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
    return "\n".join(lines)


async def _do_add_admin(telegram_id: int, added_by: int | None) -> str:
    async with session_scope() as session:
        created = await admins_repo.add_admin(
            session, telegram_id=telegram_id, added_by=added_by
        )
    if created:
        return f"✅ Админ <code>{telegram_id}</code> добавлен."
    return f"ℹ️ <code>{telegram_id}</code> уже админ."


async def _do_remove_admin(telegram_id: int, settings: Settings) -> str:
    if is_main_admin(telegram_id, settings):
        return "⛔ Главного админа (из env) удалить нельзя."
    async with session_scope() as session:
        removed = await admins_repo.remove_admin(session, telegram_id=telegram_id)
    if removed:
        return f"✅ Админ <code>{telegram_id}</code> удалён."
    return f"ℹ️ <code>{telegram_id}</code> не был админом."


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
        "",
        f"Bot: @{bot_info.username}",
        f"Mode: {settings.bot_mode}",
    ]
    await message.answer("\n".join(lines))


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
    telegram_id = _parse_telegram_id(command.args)
    if telegram_id is None:
        await message.answer("Использование: <code>/addadmin &lt;telegram_id&gt;</code>")
        return
    await message.answer(await _do_add_admin(telegram_id, user_id))


@router.message(Command("deladmin"))
async def cmd_del_admin(message: Message, settings: Settings, command: CommandObject) -> None:
    user_id = message.from_user.id if message.from_user else None
    if not await is_admin(user_id, settings):
        await message.answer("Команда доступна только администратору.")
        return
    telegram_id = _parse_telegram_id(command.args)
    if telegram_id is None:
        await message.answer("Использование: <code>/deladmin &lt;telegram_id&gt;</code>")
        return
    await message.answer(await _do_remove_admin(telegram_id, settings))


@router.callback_query(F.data == "admin:add_chat")
async def cb_add_chat(query: CallbackQuery, settings: Settings, state: FSMContext) -> None:
    if not await is_admin(query.from_user.id, settings):
        await query.answer("Только для администратора.", show_alert=True)
        return
    await state.set_state(AddChatStates.waiting_for_chat_id)
    await query.message.answer(
        "Отправьте <b>chat_id</b> чата (число, например <code>-1001234567890</code>)."
    )
    await query.answer()


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
        await query.answer("Только для администратора.", show_alert=True)
        return
    role = query.data.split(":")[-1]
    data = await state.get_data()
    chat_id = data.get("chat_id")
    await state.clear()

    if chat_id is None:
        await query.message.answer("Не удалось определить chat_id. Начните заново: /admin")
        await query.answer()
        return

    async with session_scope() as session:
        await upsert_chat(session, telegram_chat_id=chat_id, role=role)

    await query.message.answer(f"Чат <code>{chat_id}</code> сохранён с ролью <b>{role}</b>.")
    await query.answer()


@router.callback_query(F.data == "admin:admins")
async def cb_admins(query: CallbackQuery, settings: Settings) -> None:
    if not await is_admin(query.from_user.id, settings):
        await query.answer("Только для администратора.", show_alert=True)
        return
    await query.message.answer(await _render_admins(settings), reply_markup=admins_menu())
    await query.answer()


@router.callback_query(F.data == "admin:admin_add")
async def cb_admin_add(query: CallbackQuery, settings: Settings, state: FSMContext) -> None:
    if not await is_admin(query.from_user.id, settings):
        await query.answer("Только для администратора.", show_alert=True)
        return
    await state.set_state(AdminMgmtStates.waiting_for_add_id)
    await query.message.answer("Отправьте <b>telegram_id</b> нового админа.")
    await query.answer()


@router.message(StateFilter(AdminMgmtStates.waiting_for_add_id), F.text)
async def admin_add_id(message: Message, settings: Settings, state: FSMContext) -> None:
    if not await is_admin(message.from_user.id if message.from_user else None, settings):
        return
    telegram_id = _parse_telegram_id(message.text)
    await state.clear()
    if telegram_id is None:
        await message.answer("Некорректный telegram_id. Отправьте целое число.")
        return
    added_by = message.from_user.id if message.from_user else None
    await message.answer(await _do_add_admin(telegram_id, added_by))


@router.callback_query(F.data == "admin:admin_del")
async def cb_admin_del(query: CallbackQuery, settings: Settings, state: FSMContext) -> None:
    if not await is_admin(query.from_user.id, settings):
        await query.answer("Только для администратора.", show_alert=True)
        return
    await state.set_state(AdminMgmtStates.waiting_for_del_id)
    await query.message.answer("Отправьте <b>telegram_id</b> админа для удаления.")
    await query.answer()


@router.message(StateFilter(AdminMgmtStates.waiting_for_del_id), F.text)
async def admin_del_id(message: Message, settings: Settings, state: FSMContext) -> None:
    if not await is_admin(message.from_user.id if message.from_user else None, settings):
        return
    telegram_id = _parse_telegram_id(message.text)
    await state.clear()
    if telegram_id is None:
        await message.answer("Некорректный telegram_id. Отправьте целое число.")
        return
    await message.answer(await _do_remove_admin(telegram_id, settings))


@router.callback_query(F.data == "admin:admin_list")
async def cb_admin_list(query: CallbackQuery, settings: Settings) -> None:
    if not await is_admin(query.from_user.id, settings):
        await query.answer("Только для администратора.", show_alert=True)
        return
    await query.message.answer(await _render_admins(settings))
    await query.answer()


@router.callback_query(F.data == "admin:cancel")
async def cb_cancel(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await query.message.answer("Отменено.")
    await query.answer()


@router.callback_query(F.data == "admin:sync_crm")
async def cb_sync_crm(
    query: CallbackQuery,
    settings: Settings,
    hollihop: HollihopClient,
    openrouter: OpenRouterClient,
) -> None:
    if not await is_admin(query.from_user.id, settings):
        await query.answer("Только для администратора.", show_alert=True)
        return
    await query.answer("Запускаю выгрузку CRM…")
    await query.message.answer("⏳ Загружаю и векторизую данные из Hollihop…")

    result = await run_hollihop_sync(hollihop, openrouter)

    if result.status == "ok":
        by_type = ", ".join(f"{k}: {v}" for k, v in result.per_type.items()) or "нет записей"
        note = f"\n⚠️ {result.error}" if result.error else ""
        await query.message.answer(
            f"✅ CRM sync завершён. Обработано {result.records_processed} "
            f"(из них финансовых {result.financial_processed}), "
            f"векторизовано {result.chunks_processed}. {by_type}.{note}"
        )
    else:
        await query.message.answer(f"❌ Ошибка CRM sync: {result.error}")


@router.callback_query(F.data == "admin:sync_drive")
async def cb_sync_drive(
    query: CallbackQuery,
    settings: Settings,
    gdrive: GoogleDriveClient,
    openrouter: OpenRouterClient,
) -> None:
    if not await is_admin(query.from_user.id, settings):
        await query.answer("Только для администратора.", show_alert=True)
        return
    await query.answer("Запускаю загрузку Google Drive…")
    await query.message.answer("⏳ Читаю и векторизую файлы из Google Drive…")

    result = await run_drive_sync(gdrive, openrouter, settings)

    if result.status == "ok":
        by_type = ", ".join(f"{k}: {v}" for k, v in result.per_type.items()) or "нет файлов"
        note = f"\n⚠️ Часть файлов пропущена: {result.error}" if result.error else ""
        await query.message.answer(
            f"✅ Google Drive sync завершён. Файлов: {result.files_processed}, "
            f"чанков: {result.chunks_processed} (финансовых файлов {result.financial_files}). "
            f"{by_type}.{note}"
        )
    else:
        await query.message.answer(f"❌ Ошибка Google Drive sync: {result.error}")


@router.callback_query(F.data == "admin:stats")
async def cb_stats(query: CallbackQuery, settings: Settings) -> None:
    if not await is_admin(query.from_user.id, settings):
        await query.answer("Только для администратора.", show_alert=True)
        return

    try:
        async with session_scope() as session:
            roles = await count_chats_by_role(session)
            collector_count = await count_collector_messages(session)
            raw_count = await count_raw_records(session)
            chunks_by_source = await count_doc_chunks_by_source(session)
            query_count = await count_query_logs(session)
            runs = await recent_sync_runs(session, limit=3)
    except RuntimeError:
        await query.message.answer("База данных не настроена.")
        await query.answer()
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
    if runs:
        lines.append("")
        lines.append("Последние синхронизации:")
        for run in runs:
            lines.append(
                f"• {run.source} — {run.status} ({run.records_processed or 0} зап.)"
            )
    await query.message.answer("\n".join(lines))
    await query.answer()
