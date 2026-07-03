# Note Turner

Telegram-бот — корпоративный ассистент «Виртуозы». Собирает знания из CRM Hollihop и отвечает на вопросы сотрудников через OpenRouter.

Спецификация проекта (цель, требования, дорожная карта по фазам, критерии приёмки): [docs/SPEC.md](docs/SPEC.md).

## Фаза 3 (текущая)

- FastAPI + aiogram webhook на Render
- Health-check: PostgreSQL, Telegram, OpenRouter, Hollihop CRM
- Роли чатов: `assistant` (отвечает) и `collector` (собирает сообщения)
- Админ-меню `/admin`: регистрация чатов, ручная выгрузка CRM, статистика, управление админами
- Мультиадмины: главный из env + список в БД; финансовые данные помечаются и доступны только админам
- Маршрутизация моделей OpenRouter (simple/complex/fallback) и системные промпты в `config/*.yaml`
- Хранение в PostgreSQL (SQLAlchemy + Alembic)
- Команды: `/start`, `/ping`, `/status`, `/sources`, `/admin`, `/admins`, `/addadmin`, `/deladmin` (admin)
- CRM и Google Drive sync запускаются в фоне, не удерживая Telegram webhook до конца выгрузки

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
| `GDRIVE_FOLDER_ID` | ID папки или файла в Drive (см. ниже). Несколько значений через запятую |
| `GOOGLE_PROJECT_ID` | Поле `project_id` из JSON-ключа |
| `GOOGLE_SERVICE_ACCOUNT_EMAIL` | Поле `client_email` — этот email добавьте в «Поделиться» папкой |
| `GOOGLE_PRIVATE_KEY_ID` | Поле `private_key_id` из JSON-ключа |
| `GOOGLE_PRIVATE_KEY` | Поле `private_key` из JSON-ключа (можно с `\n` в одной строке) |
| `GOOGLE_CLIENT_ID` | Поле `client_id` из JSON-ключа |
| `EMBEDDING_MODEL` | Модель эмбеддингов OpenRouter (по умолч. `openai/text-embedding-3-small`) |

## Деплой на Render

1. Запушьте репозиторий на GitHub/GitLab.
2. В Render: **New → Blueprint** → укажите репозиторий → `render.yaml`.
3. После деплоя задайте env vars в Dashboard:
   - `TELEGRAM_BOT_TOKEN`
   - `ADMIN_TELEGRAM_ID`
   - `WEBHOOK_BASE_URL` = URL вашего сервиса (`https://noteturner-xxxx.onrender.com`)
   - `OPENROUTER_API_KEY`
   - `HOLLIHOP_SUBDOMAIN`, `HOLLIHOP_AUTH_KEY`
   - `GDRIVE_FOLDER_ID`, `GOOGLE_PROJECT_ID`, `GOOGLE_SERVICE_ACCOUNT_EMAIL`,
     `GOOGLE_PRIVATE_KEY_ID`, `GOOGLE_PRIVATE_KEY`, `GOOGLE_CLIENT_ID` — см. раздел Google Drive
4. Перезапустите сервис — webhook установится автоматически.
5. Проверьте: `GET https://<your-service>/health`

Сейчас проект остаётся одним Render `web`-сервисом. Выносить sync в отдельный
`worker` или `private service` стоит только если после включения фоновых sync и
улучшения retrieval бот всё ещё заметно тормозит под реальной нагрузкой.

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
| не зарегистрирован | В личке — отвечает как `assistant`; в группе — молчит |

Личный чат администратора всегда работает как `assistant`, даже без регистрации.

### Меню `/admin`

Команда `/admin` (только для администраторов) открывает меню:

- **Добавить чат** — ввод `chat_id` и выбор роли;
- **Проверить источники** — живой запрос к Hollihop и Google Drive, чтобы увидеть доступность и число видимых файлов;
- **Загрузить студентов** — карточки учеников;
- **Загрузить платежи** — платежи и балансы;
- **Загрузить лиды** — лиды и заявки;
- **Загрузить группы и расписание** — группы, связи ученик-группа и расписание;
- **Загрузить всё CRM** — полный прогон CRM в порядке: студенты, платежи, лиды, остальное;
- **Загрузить Google Drive** — чтение файлов из папки, векторизация в `doc_chunks`;
- **Статистика** — счётчики чатов, сообщений, CRM-записей, векторных чанков и запросов;
- **Админы** — добавить/удалить/показать администраторов.

### Администраторы

Главный админ задаётся в env (`ADMIN_TELEGRAM_ID`) и **не может быть удалён**.
Остальных админов добавляют существующие админы:

| Команда | Действие |
|---|---|
| `/admins` | Список администраторов |
| `/sources` | Живая проверка Hollihop и Google Drive |
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
`Answerer` использует `VectorRetriever` и подмешивает релевантные фрагменты из
корпоративных документов и CRM (с указанием источников). Для админов поддерживаются
точечные refresh-запросы в личке вроде `обнови студентов`, `обнови платежи`,
`обнови лиды` и `обнови группы`, а также прямые вопросы о покрытии локальной базы вроде
`сколько у тебя данных в векторной базе по лидам и расписанию`; в обычном
режиме бот отвечает из локально синхронизированных данных.

## Google Drive как источник знаний (RAG)

Бот может читать документы из папки Google Drive, извлекать текст (Google Docs,
Таблицы, презентации, PDF), векторизовать через OpenRouter (`/embeddings`) и хранить
векторы в PostgreSQL (`pgvector`, таблица `doc_chunks`). При вопросе ищутся ближайшие
фрагменты и подмешиваются в ответ.

Настройка доступа через **сервисный аккаунт** (боту, а не личному аккаунту):

1. В [Google Cloud Console](https://console.cloud.google.com/) создайте проект и включите
   **Google Drive API** и **Google Sheets API**.
2. **IAM & Admin → Service Accounts** → создайте аккаунт → **Keys → Add key → Create new key → JSON**.
   Скачается файл вида `virtuozy-xxxxx.json`. Откройте его в блокноте.
3. В Google Drive: папка **Virtuozy** → **Поделиться** → добавьте значение **`client_email`**
   из JSON (например `noteturner@virtuozy.iam.gserviceaccount.com`) с правом **Читатель**.
4. Заполните переменные окружения по таблице ниже.
5. В меню `/admin` нажмите **Загрузить Google Drive** для выгрузки и векторизации.

**Откуда брать значения** (из скачанного JSON и из браузера):

| Переменная в Render / `.env` | Где взять |
|---|---|
| `GDRIVE_FOLDER_ID` | ID из URL: `.../folders/ID`, `.../file/d/ID` или несколько через запятую. Ссылка вида `.../drive/project/...` **не** перечисляет файлы — укажите ID конкретных папок/файлов |
| `GOOGLE_PROJECT_ID` | В JSON: `"project_id": "virtuozy"` |
| `GOOGLE_SERVICE_ACCOUNT_EMAIL` | В JSON: `"client_email": "...@...iam.gserviceaccount.com"` |
| `GOOGLE_PRIVATE_KEY_ID` | В JSON: `"private_key_id": "a1b2c3..."` |
| `GOOGLE_PRIVATE_KEY` | В JSON: всё значение `"private_key": "-----BEGIN PRIVATE KEY-----\n..."` (можно вставить одной строкой) |
| `GOOGLE_CLIENT_ID` | В JSON: `"client_id": "123456789"` |

Это не отдельный «API key» из Credentials → API key — для доступа к вашим файлам нужен
именно **JSON сервисного аккаунта** (шаг 2 выше).

Финансовые файлы определяются эвристикой по имени (`FINANCIAL_KEYWORDS`) и, как и у
CRM, доступны в контексте только администраторам.

### Финансовые данные

Финансовые методы Hollihop (например `GetPayments`, `GetBalances`, финансовые
секции `GetEdUnits`/`GetEdUnitStudents`) выгружаются вместе с остальными и
векторизуются в `doc_chunks`. При этом schedule/group chunks (`schedule_item`,
`schedule_day`, summary по `edunit` / `edunit_student`) отделяются от финансовых
chunks (`group_payer`, `group_fiscal`), чтобы не-админы могли получать ответы по
занятиям и группам, а финансовые фрагменты по-прежнему скрывались фильтром
`search_chunks(include_financial=...)`.

Подробная матрица CRM-выгрузки и стратегия свежести данных:
[`docs/HOLLIHOP_CRM_SYNC.md`](docs/HOLLIHOP_CRM_SYNC.md).

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
├── integrations/            # OpenRouter, Hollihop, Google Drive
├── bot/
│   ├── dispatcher.py
│   ├── filters.py           # ChatRoleFilter
│   ├── middlewares/         # inject deps, chat access (роли)
│   ├── keyboards/admin.py
│   └── handlers/            # ping, admin, assistant, collector
├── config/                  # settings + routing.yaml, prompts.yaml
├── services/
│   ├── crm_sync.py          # ручная выгрузка Hollihop
│   ├── drive_sync.py        # выгрузка + векторизация Google Drive
│   └── llm/                 # router, prompts, retriever (vector), answerer
├── health/checker.py
└── db/
    ├── models.py            # chats, collector_messages, raw_records, ...
    ├── session.py
    └── repositories/
alembic/                     # миграции БД
```
