from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def admin_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Добавить чат", callback_data="admin:add_chat")
    builder.button(text="Проверить источники", callback_data="admin:check_sources")
    builder.button(text="Загрузить студентов", callback_data="admin:sync_crm:students")
    builder.button(text="Загрузить платежи", callback_data="admin:sync_crm:finance")
    builder.button(text="Загрузить лиды", callback_data="admin:sync_crm:leads")
    builder.button(text="Загрузить группы и расписание", callback_data="admin:sync_crm:groups")
    builder.button(text="Загрузить всё CRM", callback_data="admin:sync_crm:all")
    builder.button(text="Загрузить Google Drive", callback_data="admin:sync_drive")
    builder.button(text="Статистика", callback_data="admin:stats")
    builder.button(text="Админы", callback_data="admin:admins")
    builder.adjust(1)
    return builder.as_markup()


def role_choice() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="assistant", callback_data="admin:role:assistant"),
                InlineKeyboardButton(text="collector", callback_data="admin:role:collector"),
            ],
            [InlineKeyboardButton(text="Отмена", callback_data="admin:cancel")],
        ]
    )


def admins_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Добавить админа", callback_data="admin:admin_add")],
            [InlineKeyboardButton(text="Удалить админа", callback_data="admin:admin_del")],
            [InlineKeyboardButton(text="Список админов", callback_data="admin:admin_list")],
            [InlineKeyboardButton(text="Отмена", callback_data="admin:cancel")],
        ]
    )
