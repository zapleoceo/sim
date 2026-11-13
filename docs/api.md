# Sim — HTTP API (контракт админки)

## Журнал системы (трейсы)

Схема `0011` (`trace_event`, `job.trace_id`, `llm_call.trace_id`). Только owner/superadmin;
operator получает 403.

Каждый ответ `/api/*` и `/hooks/*` несёт заголовок `X-Trace-Id` (32 hex). Этот id проходит
через всю обработку: вебхук → входящее → задача `reply` → вызовы модели и контролёра →
задача `send` → отправка.

- `GET /api/activity/log` — шаги журнала. Ответ: `{items, total, page, size, retention_days, kinds, statuses}`.
  - Пагинация: `page` от 1, `size` 1–200 (по умолчанию 50).
  - Сортировка: `sort` = `started_at` | `kind` | `name` | `status` | `duration_ms` | `attempt` |
    `http_status` | `actor` | `client` | `trace_id`; `order` = `asc` | `desc` (по умолчанию
    `started_at desc`). Пустые значения идут в конце.
  - Фильтры (все в SQL):
    - `kind`, `status` — списки через запятую. Kinds: `http`, `webhook`, `inbound`, `poll`,
      `job`, `llm`, `guard`, `send`, `partner`. Statuses: `ok`, `error`, `retry`, `skipped`.
    - Подстрока без учёта регистра: `name`, `actor`, `client` (имя клиента).
    - `trace_id` — префикс.
    - Точное совпадение: `client_id`, `dialog_id`, `channel_id`, `job_id`, `http_status`.
    - Диапазоны: `min_ms`/`max_ms`, `min_attempt`.
    - `has_error`; `from`/`to` (как в activity).
    - `q` ищет сразу в шаге, ошибке, исполнителе, трейсе и имени клиента.
  - Неизвестный kind/status/sort или `min_ms > max_ms` → 422.
- `GET /api/activity/traces/{trace_id}` — один запрос целиком, от старых шагов к новым. Возвращает
  `{trace_id, started_at, finished_at, duration_ms, status, cost_usd, steps}`.
  - У шага: `offset_ms` от начала трейса, `in_trace` и `llm` (модель, токены, стоимость), если это
    вызов модели.
  - Попытки задачи, которую создал другой запрос (несколько сообщений подряд склеены в один
    ответ), тоже включаются с `in_trace=false`.
  - `status` — худший итог по последним попыткам задач: ошибка, которую исправил повтор,
    не делает трейс ошибочным.
  - Неизвестный id → 404, неверный формат → 422.

Шаг (`TraceStep`):

- `id, trace_id, kind, name, status, started_at, duration_ms, attempt, http_status`
- `actor` — админ, `worker` или тип канала
- `error` — маскированная
- `detail` — небольшой словарь, например `queue_ms`, `outcome`, `guard`, `max_attempts`, `gave_up`, `query`
- `client: {id, display_name} | null`
- `admin_id, dialog_id, channel_id, job_id, message_id, llm_call_id`

В журнал не пишутся тела запросов, cookies, заголовки и тексты сообщений. Токены, ключи,
пароли и `user:pass@` в ошибках и query string маскируются. GET-запросы самого журнала и
health-проверки не логируются.

## Единая админка: сообщения и журнал LLM

Код слит в PR #13, схема `0008`; выкладка не подтверждена.

- `GET /api/activity/messages` — сохранённые сообщения всех клиентов, все авторизованные роли.
- `GET /api/clients/{client_id}/messages` — сообщения одного клиента по всем его каналам,
  включая архивные; неизвестный клиент возвращает 404. Совпадение имени не объединяет клиентов.
- `GET /api/activity/llm` — сохранённые вызовы LLM, только owner/superadmin; operator получает 403.

Общая страница: `{items, total, limit, offset}`. `limit`: 1–200, по умолчанию 50;
`offset` >= 0. Фильтрация, подсчёт и ограничение выполняются в SQL. Сортировка —
`created_at DESC, id DESC`; независимые запросы страниц не являются снимком базы.

Общие фильтры: `persona_id`, `channel_id`, `channel_kind`, `q` (до 200 символов),
`from`, `to`. Глобальные списки также принимают `client_id`; клиентский маршрут
всегда ограничен ID из пути. Идентификаторы фильтров положительные. Даты — ISO date/datetime:
день `to` включён целиком, datetime `to` исключён; timezone приводится к UTC,
naive datetime трактуется как UTC. Неправильные даты, диапазоны и enum возвращают 422.

Сообщения дополнительно фильтруются по `direction`, `author`, `delivery`.
`q` ищет буквальную подстроку в тексте или имени клиента (символы `%`, `_`, `\`
не являются шаблоном SQL). Строка содержит обычный MessageOut, `dialog_id`,
`client: {id, display_name, avatar_url}`, `persona: {id, name}`,
`channel: {id, name, kind, account_label}` и nullable `sent_at` из постоянного receipt.
Отсутствие receipt не заменяется временем создания сообщения. Новые activity-маршруты
заменяют непустую transport error общим безопасным статусом без исходной диагностики.

MessageOut во всех существующих ответах дополнен nullable `operator_id`, `operator_name`,
`provider`, `tokens_in`, `tokens_out`, `cost_usd`. Имя оператора фиксируется при новой ручной
отправке известным админом; переименование или удаление админа сохраняет имя-снимок.
Миграция не угадывает автора старых сообщений или сообщений с телефона по текущему
оператору диалога. Расходы/токены берутся только из Message, без сопоставления LLM по времени;
`null` означает неизвестно, `0` — сохранённый ноль.

LLM-фильтры: точные `workflow` (до 32), `model` (120), `provider` (64), `has_error`;
`q` ищет буквальную подстроку в этих трёх полях. Строка содержит `id`, `created_at`,
nullable `dialog_id` и контекст клиента/персоны/канала, `workflow`, `capability`, `model`,
`provider`, `tokens_in`, `tokens_out`, `cached_tokens`, `cost_usd`, `latency_ms`,
`finish_reason`, `has_error`. Вызовы без диалога сохраняются с null-контекстом.
Исходные error, job_id, payload и промпты не возвращаются.

Это обзор сохранённых сообщений и текущего delivery, а также аудит вызовов LLM,
не неизменяемый журнал всех webhooks/переходов состояний. Чтение не меняет receipt
и не предоставляет повторную отправку. Миграция `0008` добавляет снимок автора
и индексы сортировки сообщений; откат не меняет постоянные receipt `0007`.

Срез main `7ef50b1f2481f1ed9dde03c264075ea82ba86529`, PR #1–#13 слиты.
Подтверждено состояние Git; production-версия и deployment не подтверждены.

Fencing состояния job и постоянные outbound intent/receipt входят в эту базу.
Unified admin описан выше как слитый исходный код. Проверки до выкладки:
[backend validation](ai-ui/tasks/unified-client-admin/backend-validation.md).

Базовый путь `/api`. JSON, даты ISO-8601 UTC. Авторизация — cookie `sim_session` (httpOnly,
Secure, SameSite=Lax) + заголовок `X-CSRF-Token` на POST/PUT/PATCH/DELETE (значение из
`GET /api/auth/me` → `csrf_token`). Ошибки: `{"detail": "<текст>"}` с кодом 4xx/5xx.
Списки: `{"items": [...], "total": N}`, параметры `limit` (≤200, по умолчанию 50), `offset`.

## Auth
| Метод | Путь | Тело / параметры | Ответ |
|---|---|---|---|
| GET | `/api/auth/config` | — (публичный) | `{bot_username}` — бот Telegram Login Widget; `""`, если не настроен |
| POST | `/api/auth/telegram` | поля виджета `{id, first_name?, last_name?, username?, photo_url?, auth_date, hash}` | `{user, csrf_token}` + cookie; `401` подпись/свежесть/повтор; `403 {"detail": "Доступ не выдан", "telegram_id": N}`; `429`; `503` бот не настроен |
| GET | `/api/auth/link?token=` | — (break-glass) | `303 → /` + cookie; `401` ссылка использована/просрочена |
| POST | `/api/auth/logout` | — | `204` |
| GET | `/api/auth/me` | — | `{user: {id, role, telegram_id, tg_username, display_name, photo_url}, csrf_token}` / `401` |

Вход в админку — **только через Telegram** (пароля нет). `POST /api/auth/telegram` проверяет HMAC-SHA256
подписи виджета (секрет = sha256(токен бота), сравнение за постоянное время), `auth_date` не старше
1 часа и не из будущего (допуск 60 с), `hash` одноразовый (таблица `used_login_hash`). Затем ищется
активный админ по `telegram_id`; если нет — по `tg_username` (без учёта регистра) среди ещё не
привязанных (приглашение по @username): `telegram_id` привязывается. Имя, фото и username
обновляются при каждом входе. Rate limit по (id, IP) и по IP.

Break-glass для оператора сервера: `python -m sim.cli login-link <telegram_id>` печатает одноразовый
URL `{SIM_PUBLIC_URL}/api/auth/link?token=…` на 10 минут (в БД только sha256 токена); только для
существующего активного админа.

Роли: `superadmin` (всё, что `owner`, + управление админами), `owner` (всё, кроме управления
админами), `operator` (диалоги, клиенты, воронка; без настроек персонажей/каналов).
В `SIM_SUPERADMIN_TELEGRAM_IDS` перечисляются Telegram id, которые при старте API и в
`python -m sim.seed` всегда становятся активными superadmin.

## Администраторы (только `superadmin`, мутации — с CSRF)
| Метод | Путь | Тело | Ответ |
|---|---|---|---|
| GET | `/api/admins` | — | `{items: Admin[]}` |
| POST | `/api/admins` | `{telegram_id? \| tg_username?, role, display_name?}` (нужно одно из двух) | `Admin` (`201`); `409` дубль; `422` |
| PATCH | `/api/admins/{id}` | `{role?, is_active?}` | `Admin`; деактивация отзывает сессии |
| DELETE | `/api/admins/{id}` | — | `204` |

`Admin`: `{id, role, telegram_id, tg_username, display_name, photo_url, is_active, created_at}`.
Superadmin не может понизить, деактивировать или удалить себя (`409`); последнего активного
superadmin нельзя удалить, понизить или деактивировать (`409`). Не-superadmin получает `403`.

## Персонажи
| Метод | Путь | Тело | Ответ |
|---|---|---|---|
| GET | `/api/personas` | — | `{items: Persona[]}` |
| POST | `/api/personas` | `PersonaIn` | `Persona` |
| GET/PUT/DELETE | `/api/personas/{id}` | `PersonaIn` | `Persona` / `204` (в т.ч. `birth_date` `YYYY-MM-DD` или `null`, `timezone` IANA, по умолчанию `Europe/Kyiv`; неизвестный пояс `422`; `routine` — распорядок недели, строка на дело `дни ЧЧ:ММ-ЧЧ:ММ что`, до 4000 символов, непонятная строка `422`, см. [persona-prompts.md](persona-prompts.md#распорядок-дня)) |
| GET | `/api/personas/{id}/prompt` | — | `{system, fixed}` (`fixed` пуст для совместимости) — системный промпт персонажа ровно так, как его получает модель (по сохранённым настройкам): промпт персонажа (`description`), стратегия, формат ответа (`output_contract`). В реальном вызове за ним идут история диалога и контекст разговора |
| POST | `/api/personas/{id}/preview/attachment` | multipart: `file`, `voice?` (`true` для голосового, записанного в админке) | `{kind, name, text, media_url}` — голосовое или файл пробного чата в текст: голос и аудио распознаёт Broker (`/v1/transcribe`), изображения описывает Broker `vision` (журнал: workflow `preview-vision`), текстовые файлы (`txt`, `md`, `csv`, `json`…) читаются как есть (до 8000 символов). `kind`: `voice`/`audio`/`image`/`file`; `text` — то, что прочитает персона (у голосового это сама расшифровка, у остального пометка `[изображение: …]`, `[файл …]`); файл сохраняется в медиа, `media_url` для плеера. До 10 МБ; другой тип или пустой файл — `422` |
| POST | `/api/personas/{id}/preview` | `{message, history?: [{role, text}], voice_input?: bool}` | `{reply, decision, guard, model, latency_ms, voice}` — пробный ответ без отправки, **с прогоном через контролера**; `reply` — то, что ушло бы клиенту (после исправления; `""`, если контролер заблокировал); `decision` = разобранный JSON-ответ генератора `{reply, stage, needs, next_step, handoff_to_operator, refused, stop_contact}` (или `null`, если не разобрался; тогда `guard = null`); `guard` = `{verdict: "ok"/"rewritten"/"blocked", violations: string[], model, original_text}` (`original_text` — черновик для `rewritten`/`blocked`, иначе `null`); `voice` — голосовой ответ персоны по тому же правилу, что в каналах (`domain.voice.speaks`: режим «всегда», или «зеркально» при `voice_input: true`): `{media_url, duration_s, error}` (OGG/Opus; при ошибке синтеза `media_url: null` и текст ошибки, текстовый ответ остаётся), иначе `null` |

`Persona`: `{id, name, age, gender ("female"|"male"|"other"), avatar_url, description, prohibitions,
language, model, voice_id, voice_mode, voice_style, voice_ambience, strategy_id, is_active, created_at}`. Поле `disclosure_text` удалено (миграция 0009).
`voice_id` — голос Google Gemini TTS (`Kore`, `Aoede`, …) или `""`; `voice_mode` — `off` | `mirror` | `always`;
`voice_style` — подсказка манеры речи (≤300). `voice_ambience` — шум записи голосовых: `off`/`room`/`street`/`cafe`/`random`. Неизвестный голос или `voice_mode ≠ off` без голоса → `422` ([voice.md](voice.md)).
`description` — один свободный текст (кто она, характер, биография, как пишет); `prohibitions` — «чего нельзя», по правилу на строку
(попадают в системный промпт, раздел «Чего нельзя никогда»; охраннику не передаются; по умолчанию пусто, в админке поля нет: ограничения настраиваются в симуляторе). Прежние `tagline`/`character`/`backstory`/`communication_style` удалены (миграция 0004 свела их в `description`).
`model`: `""` = цепочка Broker `chat:sales`; иначе точный id (`claude-sub/claude-sonnet-5-5`).

## Медиа-галерея персонажа

Фото, видео и видео-кружочки (video notes), привязанные к персонажу. Файлы хранятся в
`settings.media_dir` и раздаются через `/api/media/{name}`.

| Метод | Путь | Тело | Ответ |
|---|---|---|---|
| POST | `/api/personas/{id}/media` | multipart: `file`, `kind` (`photo`/`video`/`video_note`), `caption?`, `tags?` (через запятую) | `PersonaMedia` (201) |
| GET | `/api/personas/{id}/media?kind=` | — | `{items: PersonaMedia[]}` (свежие сверху; `kind` фильтрует) |
| DELETE | `/api/personas/{id}/media/{media_id}` | — | 204 |
| GET | `/api/personas/{id}/media/{media_id}/file` | — | файл (inline для изображений/видео, download для остального) |

`PersonaMedia`: `{id, persona_id, kind, storage_name, caption, tags: string[], file_url, created_at}`.
`file_url` = `/api/media/{storage_name}`.
MIME: `image/jpeg`, `image/png`, `image/webp`, `image/gif`, `video/mp4`, `video/webm`; до 50 МБ.
Загрузка и удаление — только owner/superadmin.

### Telegram capabilities

`SEND_PHOTO` — отправка фото с подписью (`client.send_file(entity, photo, caption=…)`).
`SEND_VIDEO_NOTE` — кружочек (`client.send_file(entity, video, video_note=True)`).
Оба добавлены в `TelegramUserAdapter.capabilities`.

## Промпты и настройки (`/api/settings`)

Общие для всех персонажей тексты промптов и параметры хранятся в таблице `app_setting`;
нет строки — действует значение по умолчанию из кода. Деплой их не сбрасывает.
Читать может любой вошедший, менять `owner`+ (с CSRF). Страницы админки: «Общие правила» (всё, кроме групп агентов) и «Агенты» (группы «Охранник» и «Паспортистка»).

| Метод | Путь | Тело | Ответ |
|---|---|---|---|
| GET | `/api/settings` | — | `{items: Setting[], fixed: {persona, guard, game}}`: настройки в фиксированном порядке, служебный контракт контролёра (`guard`) и словарь команд игры Sim1 (`game`, только для чтения, см. [game.md](game.md)); `persona` пуст для совместимости |
| PUT | `/api/settings/{key}` | `{value}` | `Setting`; `404` неизвестный ключ, `422` неверное значение |
| DELETE | `/api/settings/{key}` | — | `Setting` со значением по умолчанию |

`Setting`: `{key, group, label, help, kind ("text"|"int"|"capability"), min, max, value, default,
is_default, updated_at, updated_by_id}`.

Ключи: `history_limit` (6–100) — сколько реплик видят персонаж и охранник; `guard_prompt` — весь
промпт охранника одним текстом (роль, вход, правила, вердикты, JSON-формат), `guard_model` (точная
модель Broker, пусто: решает маршрут; запасной маршрут модель не закрепляет), `guard_capability`,
`guard_fallback_capability` — охранник (группа «Охранник»); `passport_prompt` (инструкция агента
«паспортистка»), `passport_model` (точная модель Broker, пусто: маршрут `chat:sales`) — паспорт лида (группа
«Паспортистка»); `output_contract` — формат ответа
персонажа (JSON, добавляется в конец её промпта), `retry_nudge` — что сказать модели, если ответ не
разобрался (группа «Служебные промпты»); `reply_debounce_s` (0–300) — пауза перед ответом; `typo_percent`, `lowercase_percent` (0–100, группа «Стиль сообщений», по умолчанию из `SIM_TYPO_PERCENT`=12 и `SIM_LOWERCASE_PERCENT`=30) — как часто код делает в ответе одну оплошность (пропущенная запятая или опечатка, не больше одной на весь ответ) и начинает сообщение с маленькой буквы;
`caption_rules` — подписи постов; `trace_retention_days`; `game_channel_id` и `game_owner_telegram_id` (группа «Игра Sim1», по умолчанию 0: игра выключена, владельца нет; см. [game.md](game.md)). Значения по умолчанию для
`guard_*capability` и `reply_debounce_s` берутся из переменных окружения `SIM_GUARD_*` и
`SIM_REPLY_DEBOUNCE_S`. Промпт персонажа — её поле `description` (см. `/api/personas`). Не
редактируется контракт согласованности управляющих полей (`fixed.guard`); если сломать JSON-формат в `output_contract` или
`guard_prompt`, ответы и вердикты перестанут разбираться.

## Стратегии (сценарии общения)
| Метод | Путь | Ответ |
|---|---|---|
| GET/POST | `/api/strategies` | `{items: Strategy[]}` / `Strategy` |
| GET/PUT/DELETE | `/api/strategies/{id}` | `Strategy` |

`Strategy`: `{id, name, goal, prompt, offer, partner_name, registration_url, stage_playbook: {<stage>: string},
rules: string[], followup_hours: number[], created_at}`. Искусственной задержки ответа нет: `reply_delay_s` удалено (миграция 0009), ответ уходит сразу после генерации и проверки контролером.
`prompt` — весь сценарий стратегии одним текстом: цель, шаги воронки, тон (ориентиры, не скрипт), попадает в стабильный системный промпт. С миграции 0012 `goal`, `stage_playbook` и `rules` склеены в `prompt` и пусты; поля остаются в API для совместимости, админка их не редактирует и пересылает как есть. В админке напоминания вводятся в днях и уходят в `followup_hours` в часах. `PUT` обновляет только присланные поля: клиент, не знающий `prompt`/`notes`, их не затирает.

## Симуляции (только `owner`+, чтение)
Тестовые диалоги админов с персонажами из бота входа (см. architecture.md, «Симуляции в боте»). Не создают клиентов, диалогов и сообщений, не попадают в инбокс и аналитику.

| Метод | Путь | Ответ |
|---|---|---|
| GET | `/api/simulations?persona_id&admin_id&limit&offset` | `{items: SimBranchRow[], total}`, свежие сверху |
| GET | `/api/simulations/{id}` | `SimBranchDetail`; 404, если ветки нет |

`SimBranchRow`: `{id, admin: {id, display_name}, persona: {id, name}, title, message_count, stage, status, created_at, last_active_at, closed_at}`.
`SimBranchDetail` = `SimBranchRow` + `{needs: string[], next_step, notes, messages: SimMessage[]}`;
`SimMessage`: `{id, role: "client"|"ai", text, model, created_at, guard: {verdict, violations, model: null, original_text} | null}`.

## Модели Broker
| GET | `/api/models` | `{items: [{id, name, subscription, available}]}` — проксирует каталог Broker |

## Каналы
| Метод | Путь | Тело | Ответ |
|---|---|---|---|
| GET | `/api/events` | — (вошедший админ) | поток Server-Sent Events: `event: dialogs` с `{"dialogs": [id…]}` при каждом изменении диалога или его сообщений (только номера), комментарий `: ping` каждые 15 с, `retry: 3000`. Админка по событию перечитывает данные обычным API |
| GET | `/api/channels` | — | `{items: Channel[]}`; у канала `has_saved_credentials`: прошлое подключение сохранило данные, переподключить можно без ввода |
| POST | `/api/channels` | `{kind, persona_id, name}` | `Channel` |
| GET | `/api/channels/{id}/credentials` | — (owner) | `{fields, saved_secrets}` для формы «Подключить»: несекретные поля (api_id, телефон, логин) значениями, секреты (`bot_token`, `password`, `api_hash`, `proxy`) только именами, их значения в браузер не уходят (пустое поле при подключении берёт сохранённое на сервере). `Cache-Control: no-store` |
| DELETE | `/api/channels/{id}` | — | `204` (отвязывает сессию) |
| POST | `/api/channels/{id}/connect` | по kind: telegram `{bot_token}`; telegram_user `{api_id, api_hash, method: "qr"|"code", phone (для code), proxy?}` (qr: ответ `pending` с `qr_data_uri`, затем опрос `state`) (все значения строками); instagram `{username, password, proxy?}`; whatsapp `{}` | `ConnectState`. Введённые данные сохраняются зашифрованно (`SIM_SECRET_KEY`, в той же `channel.session_enc` под `saved_credentials`): при повторном подключении форма подставляет сохранённое (`GET /api/channels/{id}/credentials`), пустое поле берётся из сохранённого, введённое заменяет его. У каналов, где вход прервался до этой версии, данные берутся из незавершённого входа (`pending`) |
| POST | `/api/channels/{id}/verify` | telegram_user `{code}` (код входа, затем пароль 2FA тем же полем); instagram `{code}` (2FA / challenge) | `ConnectState` |
| GET | `/api/channels/{id}/state` | — | `ConnectState` |

`Channel`: `{id, kind, name, persona_id, status, account_label, last_error, last_activity_at}`.
`status`: `new | pending | active | challenge | expired | error`.
`ConnectState`: `{status, qr_data_uri?, need?: "code"|"2fa"|"challenge", message?}`.

Вход `telegram_user`: `connect` → `status: challenge, need: "code"` (Telegram прислал код в приложение/SMS);
`verify {code}` → `active`, либо `need: "2fa"` (включён облачный пароль): тогда `verify {code: "<пароль>"}`.
Неверный код/пароль и FloodWait оставляют `challenge` с `message` по-русски; истёкший код, неверный
номер или api_id/api_hash дают `status: error` (подключение начинается заново).

## Диалоги (единый инбокс WA + TG + IG)
| Метод | Путь | Параметры / тело | Ответ |
|---|---|---|---|
| GET | `/api/dialogs` | `channel_kind?, persona_id?, stage?, status?, q?, unread_only?, needs_operator?` (только диалоги, где ИИ передал/не справился) | `{items: DialogRow[], total}` |
| GET | `/api/dialogs/{id}` | — | `{dialog, client, messages: Message[]}` |
| POST | `/api/dialogs/{id}/messages` | `{text}` | `Message` (оператор; ИИ этого диалога ставится на паузу) |
| POST | `/api/dialogs/{id}/ai` | `{paused: bool}` | `Dialog` (пауза отменяет ещё не отправленные ответы ИИ; снятие паузы сбрасывает `needs_operator`) |
| POST | `/api/dialogs/{id}/resolve` | — | `Dialog` — снимает `needs_operator`, не меняя паузу ИИ. Флаг также снимается ответом оператора и возобновлением ИИ |
| POST | `/api/dialogs/{id}/read` | — | `204` |

`DialogRow`: `{id, channel_kind, persona: {id, name, avatar_url}, client: {id, display_name, avatar_url, stage, status},
last_message: {text, direction, at}, unread: number, ai_paused}`.
`Message`: `{id, direction: "in"|"out", author: "client"|"ai"|"operator", text, media_kind?, media_url?, created_at, delivery: "queued"|"sent"|"failed"|"cancelled", model?, error?, guard}`. `cancelled` — ответ ИИ не отправлен, потому что диалог перешёл к человеку (оператор/владелец написал, пауза ИИ), клиент отказался или контролер заблокировал черновик (тогда `error` = причина). `guard` — только у сообщений ИИ, иначе `null`: `{verdict: "ok"|"rewritten"|"blocked", violations: string[], model, original_text}`; для `rewritten` `text` — отправленный исправленный текст, `original_text` — черновик генератора; для `blocked` `text` — заблокированный черновик.

Контролер сравнивает ID своей модели с ID исходного генератора без учёта регистра и пробелов
по краям. При совпадении выполняется один запрос через резервную capability; повторное
совпадение вызывает `GuardError`, и сообщение не ставится на отправку. Это действует и при
проверке исправленного ответа. Отсутствующий ID пока допускается; разные ID могут быть
алиасами одной модели, поэтому эти случаи не доказывают независимость проверки. Исправленный
ответ сверяется с моделью исходного генератора, а не с моделью, которая внесла исправление:
контролер может повторно проверить собственную правку.


## Клиенты
| Метод | Путь | Тело | Ответ |
|---|---|---|---|
| GET | `/api/clients` | `stage?, status?, persona_id?, q?` | `{items: Client[], total}` |
| GET | `/api/clients/{id}` | — | `ClientCard` (в т.ч. `passport` — паспорт лида от паспортистки, `passport_updated_at`) |
| PATCH | `/api/clients/{id}` | `{notes?, next_step?, next_step_at?, display_name?}` | `Client` |
| POST | `/api/clients/{id}/stage` | `{stage?, status?, reason?}` | `Client` (оператор; `registered`/`paid` вручную запрещены → 422) |

`Client`: `{id, display_name, avatar_url, persona_id, stage, status, next_step, next_step_at, notes,
needs: string[], channels: [{kind, dialog_id}], last_contact_at, created_at}`.
`ClientCard`: `Client + {events: FunnelEvent[], partner_events: PartnerEvent[]}`.
`FunnelEvent`: `{id, created_at, type: "stage"|"status", from_stage, to_stage, from_status, to_status, actor: "ai"|"operator"|"partner"|"system", reason}` (для `type=stage` заполнены `*_stage`, для `status` — `*_status`, остальные `null`).
`PartnerEvent`: `{id, event: "registration"|"payment", occurred_at, amount, currency, payout, external_id}`.

## Воронка
| GET | `/api/funnel` | `persona_id?` | `{stages: [{stage, count, clients: ClientMini[] (до 50)}], statuses: {active, paused, no_response, refused}}` |

## Контент Instagram

Публикация одного фото подготовлена, но по умолчанию отключена. Условия отдельного включения и ограничения повторных попыток: [Instagram content publishing](instagram-content-publishing.md). Это не готовность к производственному включению.

| Метод | Путь | Ответ |
|---|---|---|
| GET/POST | `/api/content` | `{items: ContentPost[]}` / `ContentPost` |
| PUT/DELETE | `/api/content/{id}` | `ContentPost` |
| GET | `/api/content/{id}/publication` | Только owner/superadmin: `{post_id, state: "not_attempted"|"uncertain"|"receipt_known", attempted_at?, external_id?, published_at?}`; чтение сохранённой истории, без сброса попытки |
| POST | `/api/content/{id}/approve` | `ContentPost` (только owner) |
| POST | `/api/content/{id}/generate-caption` | `{caption}` — подпись в стиле персонажа через Broker |

`ContentPost`: `{id, persona_id, channel_id, kind: "photo"|"video"|"reel"|"story", caption, media_urls,
status: "draft"|"approved"|"scheduled"|"published"|"failed", scheduled_at, published_at, error}`.

## Аналитика
| GET | `/api/analytics/summary` | `from, to` (`YYYY-MM-DD`, обе границы включительно), `persona_id?` | см. ниже |
| GET | `/api/analytics/spend` | `from, to` (`YYYY-MM-DD`, обе включительно; по умолчанию последние 30 дней) | деньги, потраченные всем проектом SIM, как их считает Broker (`GET /v1/usage`, SIM ничего не пересчитывает): `{available: true, from, to, currency, total, by_capability, by_day, limits: {daily_cap_usd, monthly_cap_usd, today_usd, month_usd}}`; если Broker не ответил — `{available: false, error}` (200). Персонаж не фильтруется: Broker считает по проекту. Подписки (Claude, Codex) оплачиваются фиксированно и в сумму не входят |

```json
{"contacts": 0, "new_contacts": 0, "replied": 0,
 "by_stage": {"first_contact": 0, "...": 0},
 "registrations": 0, "payments": 0, "revenue": 0.0, "partner_payouts": 0.0,
 "revenue_by_currency": [], "partner_payouts_by_currency": [],
 "conversion": {"contact_to_registration": 0.0, "registration_to_payment": 0.0},
 "llm": {"calls": 0, "cost_usd": 0.0, "tokens_in": 0, "tokens_out": 0},
 "guard": {"ok": 0, "rewritten": 0, "blocked": 0},
 "by_channel": [{"kind": "telegram", "contacts": 0, "registrations": 0, "payments": 0}],
 "daily": [{"date": "2026-11-05", "contacts": 0, "registrations": 0, "payments": 0}]}
```

`llm` — вызовы моделей из журнала SIM (`llm_call`); деньги страница «Аналитика» берёт не отсюда, а из `/api/analytics/spend` (данные Broker). `contacts` — уникальные клиенты с входящими сообщениями за период. `replied` — число
таких клиентов, у которых исходящее сообщение с `delivery: "sent"` и без
`guard_verdict: "blocked"` предшествует входящему в том же периоде. Повторные
входящие не увеличивают число клиентов. `queued`, `failed`, `cancelled` и
заблокированные исходящие не подтверждают обмен. Входящие учитываются независимо
от delivery и guard_verdict. Фильтр `persona_id` применяется к клиенту.
Период и порядок сообщений определяются по `created_at`, а не по фактическому
времени отправки: отдельного `sent_at` нет. Для сообщений, отправленных позже создания (ретраи, очередь),
это приближение, а не точная хронология доставки. `guard` по-прежнему считает
сохранённые вердикты за период, включая неотправленные сообщения.

`revenue_by_currency` и `partner_payouts_by_currency` — массивы `{currency, total}`.
Выручка включает только `amount` событий payment; выплаты включают `payout` всех
событий, включая registration. Валюта выплаты — `currency` того же события.
Суммы группируются после удаления пробелов по краям и приведения валюты к верхнему
регистру, без конвертации и без изменения исходных данных. Каждая сумма округляется
до двух знаков после сложения. Отсутствующая/пустая валюта — отдельная группа
`{"currency": null, "total": ...}`: её сумма не считается подтверждённой суммой в одной валюте.
Порядок: валюты по алфавиту, затем неизвестная. Неизвестные непустые коды сохраняются
как метки, без проверки ISO и без подмены USD/RUB.

Прежние поля `revenue` и `partner_payouts` остаются числом только при одной
указанной валюте соответствующего показателя. При нескольких валютах или группе
без валюты они равны `null`; потребителям следует показывать группы с валютами.
Если сумм нет, группы пустые, а старое поле равно `0.0` (без подразумеваемой валюты).
Это изменение контракта для старых клиентов: необходимо обработать `null` и перейти
на группы; обновление API и dashboard следует выкладывать совместно. Новый dashboard
при ответе старого API без групп показывает «Сводка по валютам недоступна».
`null` в amount/payout не создаёт группу, явный ноль создаёт. Дата и persona_id
фильтруют события до группировки. Расходы `llm.cost_usd` по-прежнему отдельно в USD.

## Партнёр
| POST | `/api/partner/import` | multipart CSV `client_ref,event,amount,currency,payout,occurred_at,external_id` | `{imported, skipped, errors[]}` |
| POST | `/hooks/partner` | `{client_ref, event: "registration"|"payment", external_id, amount?, currency?, payout?, occurred_at}`; заголовки `X-Sim-Timestamp`, `X-Sim-Signature: hex(hmac_sha256(secret, ts + "." + body))` | `{ok: true}` |

`client_ref` — ref-код клиента, который ИИ подставляет в ссылку регистрации (`?ref=<code>`).

Для каждой строки CSV обязателен непустой `external_id` или явный `occurred_at`.
Строка без обоих значений возвращает ошибку с номером строки и не меняет события или воронку;
остальные корректные строки продолжают импортироваться. Рекомендуется ID события от партнёра.
При наличии только даты сохраняется прежний ключ `csv:<client_ref>:<event>:<UTC timestamp>`;
при наличии только ID дата первого импорта используется как время события. Повтор того же ID
(в пределах типа события) или того же fallback-ключа учитывается в `skipped`, а не в выручке.
Для разных платежей одного клиента с одинаковой датой нужны разные `external_id`.
Исправление не удаляет и не объединяет уже созданные дубли; webhook-контракт не меняется.

## Webhooks каналов
| POST | `/hooks/telegram/{channel_id}` | Update Bot API; заголовок `X-Telegram-Bot-Api-Secret-Token` |
| POST | `/hooks/evolution/{channel_id}` | событие Evolution `messages.upsert`; заголовок `apikey`; только из docker-сети |
| POST | `/hooks/admin-bot` | Update Bot API бота входа (`message`, `callback_query`); заголовок `X-Telegram-Bot-Api-Secret-Token` = `hex(hmac_sha256(SIM_SECRET_KEY, "admin-bot:" + токен бота))`, неверный — 401. После верного секрета всегда `200 {ok: true}` (ответ модели считает job, не запрос) |

## Служебное
| GET | `/api/live` | `200 {ok:true}` — жив процесс API; без обращения к зависимостям |
| GET | `/api/ready` | `{ok, db, version}`; `200` при доступной БД, `503` при ошибке/таймауте БД (3 с) |
| GET | `/api/health` | `{ok, db, broker, version}`; статус readiness (`200`/`503`), Broker — дополнительная диагностическая проверка |

## Дополнения реализации (аддитивные поля)
`DialogRow.needs_operator` (в админке — бейдж «нужен оператор» в списке и в шапке диалога), `Dialog` (`/api/dialogs/{id}` → `dialog`: `{id, channel_id, channel_kind, persona_id, client_id, external_thread_id, ai_paused, needs_operator, operator_id, unread, created_at}`),
`Client.ref_code`, `Client.channels` содержит диалоги всех каналов клиента (лид остаётся у своей персоны; identity из других каналов той же персоны сливаются по ref-коду/телефону, см. architecture.md), `PUT /api/content/{id}` от оператора (не owner) для approved/scheduled поста возвращает его в `draft`, `Strategy.notes`, `Channel.name`. Медиа сообщений отдаётся через `GET /api/media/{name}` (нужна сессия). Хранятся только известные расширения (аудио, картинки без SVG, видео, txt/csv/json/md/pdf), остальное сохраняется как `.bin`. В браузере показываются только аудио, картинки и видео, всё прочее отдаётся как скачивание `application/octet-stream`; на всех ответах `Content-Security-Policy: sandbox; default-src 'none'` и `X-Content-Type-Options: nosniff` (`adapters/media.py`: `safe_suffix`, `serving`).
`GET /api/dialogs` фильтр по каналу — `channel_kind`. Заголовок `X-Silent` сервер игнорирует.


## Видимость истории для ИИ
История реального диалога включает входящие сообщения и только отправленные (`sent`)
исходящие сообщения ИИ или оператора. `queued`, `failed` и `cancelled` не считаются
увиденными клиентом; guard-blocked черновики также исключаются. Фильтрация происходит
до лимита последних сообщений, поэтому очередь неотправленных черновиков не вытесняет
реальные реплики. После успешной повторной отправки сообщение снова доступно истории.
Это правило не удаляет записи из inbox/админки и не меняет историю симуляций.
Метрики аналитики и гарантии внешней доставки этим изменением не пересматриваются.

## Выполнение очереди worker
Рабочий цикл держит до четырёх job одновременно. Освободившийся слот заполняется без
ожидания остальных job прежней пачки; проверки новых due job и каналов продолжаются
с интервалом `worker_tick_s`, даже если генерация ответа ещё идёт. Порядок выбора
остаётся `run_at, id`; порядок завершения параллельных job не гарантируется.
Активные job текущего worker исключаются из повторного claim до применения лимита.
При штатном SIGTERM/SIGINT новые claim прекращаются, уже взятые job и polling
дожидаются завершения. Retry/backoff, lease duration и обработчики не изменены.
Fencing состояния очереди входит в базу; истечение lease при слишком долгом обработчике
всё ещё может позволить другому процессу повторное исполнение handler. Лимит относится к одному worker,
а `tick()` остаётся отдельным ожидающим batch-вызовом для тестов/разовых запусков;
его не следует запускать одновременно с `run()` того же экземпляра.

### Постоянная попытка исходящей отправки

Job `send` использует независимые intent/receipt из миграции `0007`.
При откате после подтверждённой отправки повтор восстанавливает `sent` без нового вызова
канала. Неизвестный результат останавливает автоматические повторы: `delivery=failed`,
`needs_operator=true`; это не доказательство отсутствия доставки. Нового retry/reset API нет.
Контракт, ограничения и требования rollout: [исходящие сообщения](outbound-delivery.md).

### Fencing состояния очереди

Входит в базу PR #1–#12; схема БД и HTTP API этим изменением не менялись. Существующий `attempts`, который
увеличивается при каждом claim, используется как поколение владения. Worker сохраняет
`claim_attempt` из результата claim до выполнения handler и передаёт именно его в
`complete`/`fail`, а не значение из повторно загруженной job. Эти методы меняют строку
только при `status=running` и совпадении поколения (compare-and-set). Повторный claim
атомарно перепроверяет прежние status/attempts и условие due/expired перед увеличением
поколения. Устаревший исполнитель не перезапишет состояние более нового claim.

`complete` сообщает, применено ли обновление. `fail` возвращает true только если его
обновление применено и перевело job в terminal failed; только тогда worker вызывает
`giveup`. Fenced-out failure не запускает `giveup`, не меняет retry/error/lease нового
владельца. Retry/backoff рассчитывается по сохранённому поколению.

Истечение lease разрешает новый claim, но само по себе не отменяет результат прежнего
владельца: если никто не перехватил job, он ещё может завершить её. Lease renewal нет.
Это защита состояния очереди, не fencing handler-транзакций или внешних эффектов:
два обработчика всё ещё могут выполняться после истечения lease. Для job `send` повторный
вызов транспорта ограничен отдельным [постоянным intent](outbound-delivery.md).
Гарантии distributed exactly-once нет. Durable intent Instagram остаётся отдельной
защитой конкретного upload.

Смешанный rollout старых и новых worker небезопасен: старые процессы всё ещё могут
обновлять очередь без проверки поколения. Перед применением требуются остановка новых
claim у старых worker и полное завершение/остановка их текущих job; затем запуск только
новой версии. Отсутствие миграции не делает смешанный rollout безопасным. В этой работе
rollout не выполнялся. Исторический backend-прогон fencing: 502 теста и Ruff прошли;
это не текущая проверка production. PostgreSQL-конкурентность и внешние эффекты не проверены.

## Срез и известные ограничения аналитики

База контракта: main `349dc6648fb6808ff18c982260b40d138e3244e6` (PR #1–#12).
Описанные возможности не подтверждают текущую production-версию. В PR #9 `replied`
учитывает только `sent`, не guard-blocked исходящие перед входящим; точный контракт
и сохранённое приближение по `created_at` приведены в разделе «Аналитика» выше.
История ИИ уже была исправлена в PR #3, финансовая группировка валют в PR #8;
эти контракты сохраняются. Счётчики `guard` по-прежнему включают сохранённые вердикты
неотправленных сообщений. PR #9 слит; production-выкладка не подтверждена.
Подробнее: [состояние проекта](project-status.md), [ревью](review-2026-11-07.md) и
[durable publication intent/receipt](publication-intents.md).
