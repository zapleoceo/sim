# Sim

### Recorded agents and selected activity calls

The owner-only log and trace drawer show role/workflow, actual model and provider from
the stored LLM call, not current persona settings or the worker actor. Missing history
is labelled explicitly. The log row's step or LLM card's call ID travels with its trace
in the URL, preserving selection through Back/Forward. The selected record appears
first with the existing redacted request/response blocks expanded; remaining steps,
errors, truncation notices and copy controls remain available. No payload is rebuilt.
Tests use synthetic API fixtures; live browser visual acceptance remains separate.

### Guard independence preserves objections

A primary guard objection (block, legacy rewrite or contradictory ok) returns to
the generator even when that guard used the generator's model. Only a clean ok
requires a second independent rotation. Its approval cannot erase an earlier
objection. A repaired complete decision is checked anew before any send or handoff.
Regression coverage uses synthetic media-refusal decisions and both verdict orders;
no live models or channel messages are used.

Guard and passport use the shared `LLMResult.truncated` property for provider endings
`length` and `max_tokens` (case/whitespace normalized without changing audit metadata).
Passport updates reject those endings or output exceeding 12,000 characters
instead of silently saving a prefix. The previous complete passport and update time
remain intact; the existing job retry policy handles the failure. This does not restore
historical text already lost, or remove the documented context size bounds.

### Current-contact profile and requested media

Owner settings `media_priority_telegram_ids` (empty by default) prefer permissible
requested media without arbitrary mood refusals. Generator and guard receive the
same policy; availability, cooldown, opt-out, pauses and failures still apply.
A preference is not a delivery guarantee.

Generated photos carry the visible «Сгенерировано ИИ» caption. Newly encoded video
notes carry an in-frame `AI generated` mark inside Telegram's circular crop; encoder
cache version 2 prevents reusing old unmarked cached clips for new requests.
Already delivered or queued old clips are not rewritten by this release.

`contact_profile_channel_id` defaults to 0 (disabled). One explicitly selected
Telegram-user channel may read the **current dialog's** name, username, bio and
one small available profile photo when the latest inbound mentions a profile or
avatar. The persisted channel/client/peer identity must match. No arbitrary RPC,
other-contact lookup, account management, bulk history or security operation is
exposed. Reads have a six-second budget and 512 KiB photo limit, respect Telegram
privacy, and never fetch remote URLs. Photos reach only the already selected model
if its current Broker catalog confirms image input; no alternate-model fallback.
Diagnostics record operation/status, not profile payload or image bytes. Known
stored usernames reach text/voice context and guard even with live reads disabled.

These are bounded service tools invoked by code on profile requests, **not** an
unrestricted LLM tool/RPC interface. Existing dialog history is the only chat history
supplied. CI uses synthetic profiles, photos, transports and model responses.
Production activation requires choosing the intended existing channel; no new
credentials are created. Private/unavailable fields are never reconstructed.

### Worker recovery

Worker crash recovery uses a 90-second claim grace and a PostgreSQL execution row
lock. Live handlers retain the lock through effects and completion commit; other
workers skip the locked job even after expiry. Connection loss releases the lock;
reclaim increments the generation and rejects late old handlers before execution.
Startup never resets another worker's lease. Pre-upgrade leases retain their old
expiry. Durable send receipts still prevent blind retries of uncertain sends.
Persisting/reconciling remote Broker job IDs across a crash remains separate work.
SQLite is for development/tests; the multi-worker guarantee is checked against
disposable PostgreSQL in CI, without real connectors or provider calls.
Before Broker requests and outgoing chat transport effects, the worker checks
its original transaction/connection and generation. Loss of that connection stops
later effects; it cannot undo an already in-flight request. Durable send intents
survive rollback and prevent a second send when its acknowledgement is unknown.
The PostgreSQL gate runs the full Worker handler through virtual lease expiry and
real termination of its isolated test connection, using persisted receipts and
fake transport effects. An expired lease is described neutrally in the inbox:
it can mean either a long-running model or a pending recovery, not proof of a crash.

ИИ-персонажи (виртуальные представители): Instagram — привлечение, WhatsApp и Telegram — личное
общение, воронка до регистрации и первой оплаты у партнёра. Документированный URL: https://sim.zapleo.com.

## Состояние реализации

В коде есть персонажи, каналы, единый inbox, воронка, аналитика и симуляции. Для Instagram
подключён ограниченный путь публикации одного локального JPEG, по умолчанию выключенный и
требующий явного списка разрешённых post ID. Долговечный intent предотвращает повторный вызов
upload для того же поста, receipt позволяет восстановить локальный статус при совпадении
payload/account. Это не полная готовность публикаций из UI.
Входящие голосовые обрабатываются отдельным путём транскрипции.
Сгенерированные фото подписаны «Сгенерировано ИИ», новые кружочки содержат отметку
`AI generated` внутри кадра, без отдельного сообщения. Кружочки ИИ отправляются только на аккаунт из настройки
«Telegram id для кружочков ИИ» (Аккаунты владельца).
Аккаунты владельца (Общие правила → «Аккаунты владельца», числовые Telegram id через запятую):
с ними персонаж отвечает сразу, без задержки по распорядку и занятости, и присылает фото и
кружочки без «Паузы между медиа». Пауза перед ответом (серия сообщений) остаётся.
Строки журнала коммуникации без причины показываются без блока «Подробности события».
Карточка состояния чата показывает любой запланированный ответ: задержку по распорядку, паузу
перед ответом, ответ после снятия паузы ИИ, повтор после ошибки модели (с ошибкой и номером
попытки) и «пишет ответ сейчас». «Очистить чат» удаляет и журнал коммуникации диалога; первой
строкой остаётся запись об очистке.
Навигация админки: «Работа» (диалоги, клиенты, воронка, аналитика, активность), «Агенты»
(персонажи, каналы, симуляции, контент, студия) и «Настройка» (общие правила, помощники —
охранник, паспортистка, саммари —, стратегии, партнёр, администраторы). На телефоне разделы
агентов открываются одной кнопкой «Агенты» внизу и переключаются полосой сверху. Страница
персонажа показывает только его собственные параметры; общие настройки (формат ответа, стиль,
распорядок ответов, медиа, аккаунты владельца) правятся только в «Общих правилах», модели и
инструкции помощников — в «Помощниках». Таблицы занимают всю ширину рабочей области.
Промпт персонажа хранит версии (страница персонажа → блок «Промпт» → «Версии промпта»; каждое сохранение персонажа с другим текстом — новая версия): кто и когда сохранил
каждый текст; старую версию можно восстановить, она сохраняется как новая.

## Локально

```bash
cd backend && uv sync && cp .env.example .env   # заполнить SIM_SECRET_KEY, SIM_BROKER_PROJECT_KEY
uv run alembic upgrade head && uv run python -m sim.seed
uv run python scripts/demo_data.py                 # по желанию: демо-данные для UI, только SQLite;
                                                  # печатает одноразовую ссылку входа
uv run uvicorn sim.api.main:create_app --factory --port 8000
uv run python -m sim.worker                       # отдельный терминал
cd ../frontend && npm ci && npm run dev         # http://localhost:4200 (Vite), прокси /api и /hooks на :8000
```

Порты: API слушает `:8000`, dev-сервер админки (Vite) — `:4200` и проксирует `/api` и `/hooks`
на `:8000`; админку открывайте на `:4200`. `SIM_PUBLIC_URL` (по умолчанию `http://localhost:8000`)
— внешний адрес API для webhook Telegram и ссылки `python -m sim.cli login-link`.
`scripts/demo_data.py` строит ссылку входа от `SIM_PUBLIC_URL`, только если он задан явно,
иначе от `http://localhost:4200`.

Используйте изолированную локальную БД и тестовые настройки каналов. API и worker с действующими
учётными данными могут обращаться к внешним сервисам. Админка — React + Vite + TypeScript,
shadcn/ui, TanStack Query/Router/Table. Тесты (pytest, Vitest), сборка, линт и типы запускаются
только в CI (см. [AGENTS.md](AGENTS.md)).

## Деплой

`bash infra/deploy.sh` — тесты, сборка админки, выгрузка закоммиченного HEAD на сервер
(`/var/www/sim`), `docker compose up -d`, smoke. Секреты — только в `/var/www/sim/infra/.env` на сервере.

Скрипт меняет серверные файлы и перезапускает сервисы; не используйте его для проверки доступа.
Перед релизом нужны проверенные backup/restore и отдельная проверка PostgreSQL-сценариев.
Frontend build и health smoke не заменяют frontend tests, UX-проверку и проверку доставки.

## Подключение каналов (админка → Каналы)

- **Telegram:** создать бота у @BotFather → вставить токен. Webhook ставится сам.
- **WhatsApp:** отсканировать QR телефоном (Связанные устройства). Неофициальный клиент — риск бана номера.
- **Instagram:** логин/пароль (+ код 2FA/подтверждения). Рекомендуется прокси той же страны, что и аккаунт.

## Персонаж (админка → Агенты → Персонажи)

Каждый факт о персонаже хранится в одном месте.

- **Промпт** — один текст: кто она, легенда, характер, стиль, язык переписки, запреты. Модель
  получает его как есть; он сохраняется вместе с остальными полями одной кнопкой «Сохранить персонажа».
  Отдельных полей «описание», «запреты», «возраст» нет (миграция 0033 перенесла их текст в
  промпт, а копию распорядка из промпта убрала).
- **Поля** — то, что считает код: дата рождения (из неё возраст, знак зодиака, дни до дня
  рождения), часовой пояс (местное время), распорядок (где она сейчас), голос и звонки, язык
  голоса, модель, стратегия, пол (для фото). В промпт это не пишется; если в тексте найдены дата
  рождения, возраст или распорядок, страница предупреждает.
- Страница персонажа: «Профиль», «Промпт», «Распорядок», «Голос и звонки», «Модель и стратегия»,
  «Фото и кружочки». Собранного «Контекста модели» больше нет; общие куски правятся в «Общих
  правилах» и «Помощниках».

## Звонки (Telegram-аккаунт, AIbroker /v1/live)

Персонаж отвечает на звонок клиента, с которым уже переписывается, если у него включён приём
звонков и выбран голос. Порядок: пауза перед ответом `SIM_CALL_RING_MIN_S`–`SIM_CALL_RING_MAX_S`
(4–9 с, как человек тянется к телефону), **одновременно** открывается голосовая сессия
(ожидание готовности до 30 с, при отказе — один повтор, пока телефон звонит), затем трубка
снимается. Каждая её реплика проверяется контролёром до того, как прозвучит; первое
фиксированное «Алло» звучит без проверки. Если проверка технически не удалась, пропадает
только эта реплика, звонок продолжается; два таких сбоя подряд или запрет контролёра
завершают звонок.

Всё, что сформировало ответ, видно по каждому звонку:

- в ленте диалога — запись журнала **«Ход звонка»**: паузу и её настройку, открытие сессии
  (время, модель, попытки, отказы), снятие трубки, реплики клиента, каждую проверку её реплики
  (время, вердикт), перебивания, причину окончания и итоговые счётчики. Персонаж эту запись
  не читает;
- в **Системном журнале** — шаг вида «Звонок» с теми же шагами; проверки реплик и вызовы
  модели этого звонка лежат в той же трассе;
- **«Проблема со звонком»** пишется только при технической причине, и в ней указана сама
  причина (голосовой сервис не открыл разговор / соединение со звонящим не установилось /
  трубку снять не удалось). Если клиент сам положил трубку до ответа, это не проблема: видно
  только в «Ходе звонка».

## Системный журнал: тело запроса

В панели трассы (`/activity` → строка журнала → «Путь запроса») у шагов «Вебхук» и «Запрос
API» с методом POST/PUT/PATCH/DELETE есть раскрываемый блок **«Тело запроса»**: JSON (или
текст/поля формы) с кнопкой «Копировать тело» и пометкой, если тело усечено. Как это хранится
(`sim/services/request_body.py`, `detail.request_body` шага):

- значения ключей вида password/token/secret/key/code/session/hash/cookie/authorization/csrf
  скрываются на любой глубине, остальные строки проходят ту же маскировку, что запросы брокера;
- лимит 16 КБ, дальше тело обрезается с флагом усечения; тела больше 1 МБ не читаются;
- файлы и бинарные тела (multipart и т.п.) не сохраняются — только тип и размер;
- вход и авторизация (`/api/auth/*`, включая вход через Telegram, и `/api/oauth/*`) — только
  пометка «тело не сохраняется»;
- GET-запросы тела не имеют; в списке журнала тело не отдаётся, только в трассе.

## Ограничения доставки

Generation/CAS fencing очереди и долговечный intent/receipt исходящих сообщений не обещают
exactly-once внешних действий; смешанный rollout со старыми worker небезопасен.
