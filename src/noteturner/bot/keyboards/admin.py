from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def admin_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Добавить чат", callback_data="admin:add_chat")
    builder.button(text="Загрузить CRM", callback_data="admin:sync_crm")
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
