from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Привет! Я <b>Note Turner</b> — корпоративный ассистент «Виртуозы».\n\n"
        "• В личке — просто напишите вопрос.\n"
        "• В группе — упомяните меня через @.\n\n"
        "Команды: /ping, /myid, /status (admin), /admin (admin), /admins (admin)"
    )


@router.message(Command("myid"))
async def cmd_myid(message: Message) -> None:
    user = message.from_user
    if user is None:
        await message.answer("Не удалось определить пользователя.")
        return
    username = f"@{user.username}" if user.username else "не задан"
    await message.answer(
        f"Ваш Telegram ID: <code>{user.id}</code>\n"
        f"Username: {username}"
    )


@router.message(Command("ping"))
async def cmd_ping(message: Message) -> None:
    await message.answer("pong")
