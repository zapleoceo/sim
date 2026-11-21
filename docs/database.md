# База данных SIM v2

PostgreSQL 16. Модели — `backend/sim/db/models.py`, миграции Alembic с `0001`. У каждой таблицы
есть `id` (bigint), `created_at`, `updated_at` (Advanced Alchemy `BigIntAuditBase`, время в UTC).
Перечисления хранятся строками с CHECK-ограничением (`sim/domain/enums.py`): новое значение —
правка кода и маленькая миграция, без `ALTER TYPE`. Медиафайлы лежат в томе (`SIM_MEDIA_DIR`),
в базе только ключ.

## Доступ

### `admin_user`
Кто может открыть дашборд. Вход только через Telegram Login.
`telegram_id` (уникальный), `name`, `username`, `is_active`. Список админов поднимается из
`SIM_SUPERADMIN_TELEGRAM_IDS`.

### `auth_session`
Сессия дашборда: `admin_id`, `token_hash` (sha256 cookie, уникальный), `csrf_token`,
`expires_at`.

## Персонаж и каналы

### `persona`
Персонаж (их может быть несколько, [personas.md](personas.md)): `name` (уникальное), `gender`, `language`, `timezone`,
`birth_date`, `avatar_media_id`, единый `prompt`.
Модели Broker по назначению (`""` — не выбрана): `model_reply` (ответы), `model_guard`
(проверка; пусто — используется `model_reply`), `model_vision` (фото/видео/PDF),
`model_transcribe` (голосовые), `model_memory` (сводка и память; пусто — `model_reply`),
`model_tts`, `model_image`, `model_video`, `model_live`; голос `voice_id` (из
`sim/services/voices.py`) и `voice_style` — обычная манера речи в 2–4 словах («тепло, естественно, разговорно»), не описание: TTS получает её как подсказку и длинный текст начинает произносить.
`guard_rules` — короткий список запретов для проверки. Переключатели `ai_enabled`,
`voice_enabled`, `photo_enabled`, `video_enabled`, `calls_enabled`. `reply_delay_s` — сколько
ждать пачку сообщений перед ответом.
Жизнь (миграция `0003`, `docs/persona-life.md`): `life` (JSONB, `{}` — мира нет: распорядок,
люди, увлечения, места по схеме `sim/domain/life.PersonaLife`), `model_life` (планировщик дня;
пусто — дни не планируются), `appearance` (точное описание внешности по-английски для фото и
видео), `voice_ambience_enabled` (фон сцены в голосовых, по умолчанию да).
Миграция `0004` ([personas.md](personas.md), [leads.md](leads.md)): `model_passport`
(паспортистка; пусто — `model_memory`, затем `model_reply`), `is_active` (по умолчанию да;
`false` — архив: никому не отвечает, дни не планируются, аккаунты Telegram отключаются).
Новый персонаж создаётся клонированием (`sim/services/persona_clone.py`).
Кто использует: ответ и проверка (`reply.py`, `guard.py`), распознавание, генерация, память,
`capabilities.py` (что можно в этом ходе).

### `persona_arc`
История её жизни (миграция `0003`): `persona_id`, `title`, `summary`, `stage` (где история
сейчас), `status` (`active | paused | done`), `started_on`, `ends_on`. Правит владелец; `stage`
и `status` двигает планировщик дня. Активные — в блоке контекста «Твои истории сейчас».

### `persona_day`
Спланированный день по её местному календарю (миграция `0003`): `persona_id`, `day`
(уникально `(persona_id, day)` — день планируется один раз), `events` (`[DayEvent]`: `start`,
`end`, `title`, `place`, `with_whom`, `busy`, `details`, `outfit`, `ambience`), `summary` (как
прошёл), `source` (`model | edited`), `model`, `error` (план не получился — событий нет).
Пишут планировщик (`sim/services/life/planner.py`) и дашборд (правка → `edited`).

### `persona_canon`
Факт о себе, который она придумала в переписке (миграция `0003`): `persona_id`, `fact`,
`chat_id`, `event_id` (сообщение-триггер), `status` (`active | rejected`). Пишет ход ответа
(`canon` плана, без дублей); активные видны во всех чатах; владелец правит и отклоняет.

### `channel`
Аккаунт мессенджера, через который говорит персонаж: `persona_id`, `kind` (`telegram_user`),
`title`, `status` (`new | connected | error | disabled`), `account_id`, `account_label`,
`credentials_enc` (Fernet: api_id, api_hash, сессия, прокси), `last_error`,
`last_activity_at`. Входящие сообщения приходят с `channel_id` (`Inbox`). `credentials_enc`
пишет вход по QR из дашборда (`sim/api/routes/channels.py`); процесс `telegram` берёт каналы
активных персонажей с учётными данными и статусом не `disabled`.

## Люди и чаты

### `client`
Собеседник (лид): `persona_id` (персонаж канала, куда он написал впервые), `name`, `username`,
`phone`, `timezone` (старое поле профиля; часовой пояс теперь в паспорте), `notes` (заметки
оператора, агент их видит), `ai_enabled` (**по умолчанию выключен**: новый клиент
сохраняется, но персонаж ему не отвечает), `passport` (JSONB, миграция `0004`, `{}` — пусто):
`{text, name, age, gender, city, country, timezone, occupation, stage, temperature,
next_step, updated_at, locked_fields}` ([leads.md](leads.md)), `last_seen_at`.
`Inbox` создаёт клиента при первом контакте и обновляет `last_seen_at`; паспорт пишут
паспортистка (`sim/services/passport.py`) и оператор (`PATCH /clients/{id}`, поля блокируются).
`memory` удалена в `0004`, её факты перенесены в текст паспорта.

### `client_identity`
Идентификатор клиента в мессенджере: `client_id`, `channel_kind`, `external_id`,
`access_hash` (последний увиденный; у каждого аккаунта он свой — для отправки берётся
`chat.peer_access_hash`). Уникально `(channel_kind, external_id)` — один человек в двух
мессенджерах или у двух персонажей остаётся одним клиентом. `Inbox.resolve_chat` ищет
клиента по этой таблице.

### `chat`
Переписка клиента через канал: `persona_id` (= персонаж канала; он и отвечает в чате),
`client_id`, `channel_id` (уникально `(channel_id, client_id)`), `peer_access_hash` (access hash
клиента так, как его видит аккаунт этого чата; `0004`), `ai_enabled` (переключатель чата), `needs_operator` (ставится после
второго блока проверки; пока стоит, персонаж молчит), `unread_count`, `last_message_at`,
`summary` и `summary_until_event_id` — сводка старой части ленты и граница: события с
`id <= summary_until_event_id` описаны сводкой, после неё — идут в ленту дословно.
Строка чата блокируется (`FOR UPDATE`) в начале и в конце хода и при записи памяти.

### `message`
Доставляемое сообщение в любую сторону: `chat_id`, `direction` (`in | out`), `author`
(`client | persona | operator`), `kind` (`text | photo | video | voice | video_note | file |
sticker`), `text` (текст или подпись к медиа; у голосового персонажа пусто — сказанное лежит в
`media_asset.description`), `media_id`, `status` (`received` у входящих; `pending → sent |
failed` у исходящих), `external_id` (id в мессенджере; уникально `(chat_id, direction,
external_id)` — это и есть идемпотентность входящих), `reply_to_external_id`, `reply_run_id`
(какой ход его написал), `error`, `sent_at`.
Пишут: `Inbox` (входящие), `outbox.enqueue_message` (исходящие `pending`), процесс `telegram`
через `outbox.mark_sent` / `mark_failed`.

## Медиа

### `media_asset`
Один файл — одна строка, для всего: медиатека персонажа, сгенерированное, присланное
клиентом. `persona_id` (у файлов клиента `NULL`), `source` (`library | generated | incoming`),
`kind` (`photo | video | audio | file`, по mime), `is_reference` (фото внешности для генерации),
`storage_key` (уникальный), `mime`, `file_name`, `size_bytes`, `sha256`, `width`, `height`,
`duration_s`, `caption`, `tags`, `description` (что распознала модель или что сказано в
голосовом), `understanding` (`pending | done | failed | skipped`), `understanding_error`,
`generation_id`.
Пишет только `media_library.add_media` (и `update_media` / `delete_media`). Каталог для
модели (`catalog_for_chat`): фото и видео персонажа из `library` и `generated`, которые ещё не
уходили в этот чат (исходящее сообщение `sent` или `pending`; `failed` не считается).

## Работа агента

### `reply_run`
Один ход персонажа: `chat_id`, `status` (`running | sent | blocked | skipped | failed`),
`trigger_event_id` (последнее сообщение клиента, на которое отвечали), `context` (снимок того,
что ушло в модель: факты, карточка, сводка, лента, id событий, каталог), `plan` (итоговый
JSON-план), `guard_verdicts` (`[{ok, reason, attempt}]`), `attempts` (сколько планов писала
модель: 1 или 2), `error` (почему не отправлено, или пометка вроде «голосовое отправлено
текстом»), `finished_at`.
Ходы `sent` и `blocked` считаются ответом на сообщения до `trigger_event_id`; `skipped` и
`failed` — нет.

### `generation`
Фото, видео или кружок, которые попросил персонаж: `persona_id`, `chat_id`, `reply_run_id`,
`kind` (`photo | video | voice | video_note`; `video_note` — круглое видеосообщение, миграция
`0002`), `prompt`, `speech` (точные слова, которые она говорит в кадре; пусто — без речи;
`0002`), `caption`, `tags`, `ref_media_ids` (референсы: до 4 фото внешности + до 2 фото той же
сцены), `scene` (сцена, в которой сделано: `{day, event_id, place, outfit, ambience, shot}`,
`0003`; по ней находятся фото того же события для непрерывности), `status`
(`queued | running | done | failed`), `model`, `result_media_id`, `broker_job_id`, `cost_usd`,
`error`, `finished_at`. Создаёт обработчик действия, выполняет задача `agent.run_generation`.
Результат кружка — `media_asset` вида `video` (квадрат 640×640), сообщение вида `video_note`.

### `call`
Звонок (этап «Звонки»): `chat_id`, `direction`, `status` (`ringing | active | ended | missed |
declined | failed`), `external_id`, `started_at`, `ended_at`, `duration_s`, `transcript`
(`[{who, text, t}]`), `model`, `cost_usd`, `error`. В контексте — строка ленты с расшифровкой;
идущий звонок отмечается в фактах.

### `chat_event`
Журнал чата — **единственный источник** и для ленты дашборда, и для контекста агента
(`docs/context.md`). Только дополняется. `chat_id`, `kind` (`message_in`, `message_out`,
`message_edited`, `message_deleted`, `media_understood`, `send_failed`, `call`,
`generation_started`, `generation_failed`, `guard_blocked`, `operator_needed`, `ai_toggled`,
`operator_note`, `client_updated`, `memory_updated`), `in_context` (видит ли агент), ссылки
`message_id`, `media_id`, `call_id`, `generation_id`, `reply_run_id`, `actor`
(`client | persona | operator | guard | system`), `data` (мелкие факты для отображения: было/стало,
причина блокировки, текст удалённого и т. п.).
Пишется только через `sim.events.append_event` в той же транзакции, что и изменение; после
коммита Postgres шлёт `NOTIFY sim_events` для живого обновления дашборда.

### `llm_call`
Каждый вызов Broker: `reply_run_id` (если в рамках хода), `chat_id`, `purpose` (`reply | guard |
vision | transcribe | document | tts | image | video | live | memory | life | passport`; `life` —
планировщик дня, `0003`; `passport` — паспортистка, `0004`), `model`, `request`
(без байтов: вместо файлов mime и размер), `response`, `tokens_in`, `tokens_out`, `cost_usd`,
`latency_ms`, `error`. Пишет `sim/services/llm_log.logged_call` в отдельной короткой транзакции.
В дашборде — под каждым ответом персонажа («что ушло в модель и что вернулось»).

## Очередь задач

Таблицы `procrastinate_*` создаёт Procrastinate своей миграцией схемы. Там лежат задачи
(`agent.*`, `telegram.*`), их блокировки (`lock`, `queueing_lock`) и история запусков. Порядок и
блокировки описаны в `docs/reply.md`, п. 2.
