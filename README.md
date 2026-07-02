# Note Turner

Telegram-бот — корпоративный ассистент «Виртуозы». Собирает знания из CRM Hollihop и отвечает на вопросы сотрудников через OpenRouter.

## Фаза 1 (текущая)

- FastAPI + aiogram webhook на Render
- Health-check: PostgreSQL, Telegram, OpenRouter, Hollihop CRM
- Бот отвечает в **личке** и в **группе при @упоминании**
- Команды: `/start`, `/ping`, `/status` (admin)

## Быстрый старт (локально)

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -e ".[dev]"

copy .env.example .env
# Заполните TELEGRAM_BOT_TOKEN, OPENROUTER_API_KEY и др.

# Локально удобнее polling:
set BOT_MODE=polling
uvicorn noteturner.main:app --reload --app-dir src
```

## Переменные окружения

| Переменная | Описание |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Токен от @BotFather |
| `TELEGRAM_WEBHOOK_SECRET` | Секрет для URL webhook |
| `ADMIN_TELEGRAM_ID` | Telegram ID администратора |
| `WEBHOOK_BASE_URL` | Публичный URL сервиса (Render) |
| `BOT_MODE` | `webhook` (prod) или `polling` (local) |
| `DATABASE_URL` | PostgreSQL connection string |
| `OPENROUTER_API_KEY` | Ключ OpenRouter |
| `HOLLIHOP_SUBDOMAIN` | Субдомен CRM (`school` → `school.t8s.ru`) |
| `HOLLIHOP_AUTH_KEY` | API-ключ (Настройки → Интеграция → API) |

## Деплой на Render

1. Запушьте репозиторий на GitHub/GitLab.
2. В Render: **New → Blueprint** → укажите репозиторий → `render.yaml`.
3. После деплоя задайте env vars в Dashboard:
   - `TELEGRAM_BOT_TOKEN`
   - `ADMIN_TELEGRAM_ID`
   - `WEBHOOK_BASE_URL` = URL вашего сервиса (`https://noteturner-xxxx.onrender.com`)
   - `OPENROUTER_API_KEY`
   - `HOLLIHOP_SUBDOMAIN`, `HOLLIHOP_AUTH_KEY`
4. Перезапустите сервис — webhook установится автоматически.
5. Проверьте: `GET https://<your-service>/health`

## Поведение бота

| Контекст | Когда отвечает |
|---|---|
| Личные сообщения | На любой текст (не команда) |
| Группа / супергруппа | Только если бот @упомянут |
| Канал | Игнорирует |

## Hollihop API

Документация: [Hollihop API 2.0](https://hollipedia.t8s.ru/books/api/page/hollihop-api-20)

```
GET https://<subdomain>.t8s.ru/Api/V2/GetLocations?authkey=<key>
```

## Структура

```
src/noteturner/
├── main.py              # FastAPI + webhook
├── config/settings.py
├── integrations/        # OpenRouter, Hollihop
├── bot/handlers/        # ping, messages, admin
├── health/checker.py
└── db/session.py
```
