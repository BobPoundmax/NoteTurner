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
        "Команды: /ping, /status (admin)"
    )


@router.message(Command("ping"))
async def cmd_ping(message: Message) -> None:
    await message.answer("pong")
