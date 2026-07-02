# Note Turner

Telegram-бот — корпоративный ассистент «Виртуозы». Собирает знания из CRM Hollihop и отвечает на вопросы сотрудников через OpenRouter.

## Фаза 3 (текущая)

- FastAPI + aiogram webhook на Render
- Health-check: PostgreSQL, Telegram, OpenRouter, Hollihop CRM
- Роли чатов: `assistant` (отвечает) и `collector` (собирает сообщения)
- Админ-меню `/admin`: регистрация чатов, ручная выгрузка CRM, статистика, управление админами
- Мультиадмины: главный из env + список в БД; финансовые данные помечаются и доступны только админам
- Маршрутизация моделей OpenRouter (simple/complex/fallback) и системные промпты в `config/*.yaml`
- Хранение в PostgreSQL (SQLAlchemy + Alembic)
- Команды: `/start`, `/ping`, `/status`, `/admin`, `/admins`, `/addadmin`, `/deladmin` (admin)

## Быстрый старт (локально)

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -e ".[dev]"

copy .env.example .env
# Заполните TELEGRAM_BOT_TOKEN, OPENROUTER_API_KEY и др.

# Примените миграции БД (нужен доступный DATABASE_URL):
alembic upgrade head

# Локально удобнее polling:
set BOT_MODE=polling
uvicorn noteturner.main:app --reload --app-dir src
```

На Render миграции применяются автоматически при старте контейнера
(`alembic upgrade head` в `Dockerfile`).

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

## Роли чатов и админ-панель

Чаты регистрируются администратором и получают роль:

| Роль | Поведение |
|---|---|
| `assistant` | Отвечает на вопросы через OpenRouter |
| `collector` | Молча сохраняет сообщения в БД |
| не зарегистрирован | В личке — просит обратиться к админу; в группе — молчит |

Личный чат администратора всегда работает как `assistant`, даже без регистрации.

### Меню `/admin`

Команда `/admin` (только для администраторов) открывает меню:

- **Добавить чат** — ввод `chat_id` и выбор роли;
- **Загрузить CRM** — ручная выгрузка Hollihop в `raw_records` (лиды, ученики и финансы);
- **Статистика** — счётчики чатов, сообщений, CRM-записей и запросов;
- **Админы** — добавить/удалить/показать администраторов.

### Администраторы

Главный админ задаётся в env (`ADMIN_TELEGRAM_ID`) и **не может быть удалён**.
Остальных админов добавляют существующие админы:

| Команда | Действие |
|---|---|
| `/admins` | Список администраторов |
| `/addadmin <telegram_id>` | Добавить админа |
| `/deladmin <telegram_id>` | Удалить админа (кроме главного) |

То же доступно через кнопку **Админы** в меню `/admin`.

### Промпты и выбор модели

Ответы ассистента собирает `Answerer` ([`services/llm/`](src/noteturner/services/llm/)):
классифицирует вопрос (simple/complex), берёт системный промпт и перебирает модели
с fallback. Всё настраивается в YAML без правки кода:

- [`config/routing.yaml`](src/noteturner/config/routing.yaml) — модели для `simple`/`complex`,
  ключевые слова и порог длины, общий `fallback`;
- [`config/prompts.yaml`](src/noteturner/config/prompts.yaml) — системный промпт «Виртуозы».

Простые/короткие вопросы идут на дешёвую модель, аналитические — на более сильную.
При ошибке модели используется следующая в списке, затем общий `fallback`.
Подмешивание корпоративных данных (векторный поиск) появится в Фазе 4 — сейчас
`Answerer` использует `NullRetriever` (ответы без внешнего контекста).

### Финансовые данные

Финансовые методы Hollihop (например `GetPayments`) выгружаются вместе с
остальными, но помечаются в `raw_records` флагом `is_financial`. Такие записи
доступны только администраторам — фильтр `get_raw_records(include_financial=...)`
скрывает их для не-админов (будет задействован при подключении RAG на Фазе 4).

## Hollihop API

Документация: [Hollihop API 2.0](https://hollipedia.t8s.ru/books/api/page/hollihop-api-20)

```
GET https://<subdomain>.t8s.ru/Api/V2/GetLocations?authkey=<key>
```

## Структура

```
src/noteturner/
├── main.py                  # FastAPI + webhook
├── config/settings.py
├── integrations/            # OpenRouter, Hollihop
├── bot/
│   ├── dispatcher.py
│   ├── filters.py           # ChatRoleFilter
│   ├── middlewares/         # inject deps, chat access (роли)
│   ├── keyboards/admin.py
│   └── handlers/            # ping, admin, assistant, collector
├── config/                  # settings + routing.yaml, prompts.yaml
├── services/
│   ├── crm_sync.py          # ручная выгрузка Hollihop
│   └── llm/                 # router, prompts, retriever, answerer
├── health/checker.py
└── db/
    ├── models.py            # chats, collector_messages, raw_records, ...
    ├── session.py
    └── repositories/
alembic/                     # миграции БД
```
