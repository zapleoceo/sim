# HTTP API дашборда (контракт бэкенда и фронта)

Все пути под `/api`. JSON, время в ISO 8601 UTC. Авторизация — cookie `sim_session`
(httpOnly, SameSite=Lax). Любой не-GET запрос несёт заголовок `X-CSRF-Token` из ответа
`/api/auth/me`. Ошибка — `{"error": "текст для человека"}` с кодом 4xx/5xx.

## Вход

| Метод | Путь | Тело / ответ |
|---|---|---|
| GET | `/auth/config` | → `{bot_username}` (для Telegram Login Widget) |
| POST | `/auth/telegram` | поля виджета (`id, first_name, last_name, username, photo_url, auth_date, hash`) → `{admin, csrf}` + cookie |
| GET | `/auth/me` | → `{admin: {id, name, username}, csrf}` или 401 |
| POST | `/auth/logout` | → 204 |

## Чаты

| Метод | Путь | |
|---|---|---|
| GET | `/chats?q=&limit=50&before=<last_message_at>` | → `{items: [ChatRow]}` |
| GET | `/chats/{id}` | → `ChatRow` + `client: Client` |
| GET | `/chats/{id}/timeline?before_id=&limit=50` | → `{items: [TimelineItem]}` от новых к старым |
| POST | `/chats/{id}/messages` | `{text}` или multipart (`file`, `caption`) — пишет оператор от имени персонажа → `TimelineItem` |
| POST | `/chats/{id}/ai` | `{enabled}` → `ChatRow` |
| POST | `/chats/{id}/read` | → 204 (обнуляет `unread_count`) |
| POST | `/chats/{id}/notes` | `{text}` — заметка для агента (`operator_note`) → `TimelineItem` |
| POST | `/chats/{id}/operator-done` | снимает «нужен оператор» → `ChatRow` |
| GET | `/reply-runs/{id}` | → `{id, status, context, plan, guard_verdicts, error, llm_calls: [LlmCall]}` |

```ts
ChatRow = {id, client: {id, name, username, ai_enabled}, ai_enabled, needs_operator,
           unread_count, last_message_at, last_message: {kind, direction, author, text} | null}
TimelineItem = {id, kind, actor, created_at, in_context, reply_run_id,
                message?: {id, direction, author, kind, text, status, error, sent_at,
                           media?: Media},
                call?: {id, direction, status, duration_s, transcript: [{who, text, t}]},
                generation?: {id, kind, status, caption, error},
                data: object}
Media = {id, kind, mime, url, caption, tags, description, source, is_reference,
         width, height, duration_s, file_name}      // url = /api/media/{id}/file
LlmCall = {id, purpose, model, request, response, tokens_in, tokens_out, cost_usd,
           latency_ms, error, created_at}
```

## Персонаж

| Метод | Путь | |
|---|---|---|
| GET | `/persona` | → `Persona` (персонаж пока один) |
| PUT | `/persona` | частичное обновление полей `Persona` → `Persona` |
| GET | `/models` | → `{items: [{id, available, input_modalities, output_modalities, capabilities}]}` из Broker |
| GET | `/voices` | → `{items: [{id, label}]}` голоса Gemini TTS |
| GET | `/instructions` | → `{text}` правила формата ответа (только чтение) |
| GET | `/channels` | → `{items: [{id, kind, title, status, account_label, last_error, last_activity_at}]}` |

`Persona` — поля таблицы `persona` (`docs/database.md`), кроме служебных и `life` (свой путь
ниже); в том числе `model_life`, `appearance`, `voice_ambience_enabled`.

| Метод | Путь | |
|---|---|---|
| POST | `/persona/appearance/describe` | модель распознавания описывает внешность по фото-референсам → `{text, references}`; ничего не сохраняет (текст правят и сохраняют через `PUT /persona` `{appearance}`); 400 — нет модели распознавания или референсов |

## Жизнь персонажа

Логика — [persona-life.md](persona-life.md).

| Метод | Путь | |
|---|---|---|
| GET | `/persona/life` | → `PersonaLife` |
| PUT | `/persona/life` | `PersonaLife` целиком → `PersonaLife`; 400 «Неверные данные: …» |
| GET | `/persona/arcs` | → `{items: [Arc]}` |
| POST | `/persona/arcs` | `{title, summary?, stage?, status?, started_on?, ends_on?}` → `Arc` (201; `started_on` по умолчанию — её сегодня) |
| PATCH | `/persona/arcs/{id}` | любые поля `Arc` → `Arc` |
| DELETE | `/persona/arcs/{id}` | → 204 |
| GET | `/persona/days?from=&to=` | → `{today, now_minute, items: [Day]}`; по умолчанию вчера…завтра по её календарю, не больше 62 дней |
| PUT | `/persona/days/{YYYY-MM-DD}` | `{events: [DayEvent], summary?}` → `Day` (`source = edited`; 400 — время не по порядку, наложения) |
| POST | `/persona/days/{YYYY-MM-DD}/plan` | спланировать заново (задача `agent.plan_day`, `force`) → 202 `{queued, day}`; 400 — не выбрана модель «Жизнь» |
| GET | `/persona/canon?status=active\|rejected` | → `{items: [Canon]}` от новых к старым |
| PATCH | `/persona/canon/{id}` | `{fact?, status?}` → `Canon` |

```ts
PersonaLife = {routine: [{days: number[] /* 0 = пн */, start: "ЧЧ:ММ", end: "ЧЧ:ММ",
               activity, place, with_whom: string[], busy: "free"|"partial"|"away",
               outfit, ambience: Ambience | null}],
               people: [{name, relation, traits, contact, notes}],
               hobbies: [{name, details, frequency}], places: [{name, kind, details}]}
Ambience = "none"|"quiet_home"|"kitchen"|"street"|"park"|"cafe"|"car"|"transport"|"office"
Arc = {id, title, summary, stage, status: "active"|"paused"|"done", started_on, ends_on,
       updated_at}
DayEvent = {start, end, title, place, with_whom, busy, details, outfit, ambience}
Day = {day, events: [DayEvent], summary, source: "model"|"edited", model, error, updated_at}
Canon = {id, fact, status: "active"|"rejected", chat_id, event_id, created_at}
```

## Медиатека

| Метод | Путь | |
|---|---|---|
| GET | `/media?source=library\|generated&kind=photo\|video` | → `{items: [Media]}` |
| POST | `/media` | multipart: `file`, `caption`, `tags` (через запятую), `is_reference` → `Media` |
| PATCH | `/media/{id}` | `{caption?, tags?, is_reference?}` → `Media` |
| DELETE | `/media/{id}` | → 204 |
| GET | `/media/{id}/file` | содержимое файла (только для вошедших) |

## Клиенты

| Метод | Путь | |
|---|---|---|
| GET | `/clients?q=` | → `{items: [Client & {chats_count}]}` |
| GET | `/clients/{id}` | → `Client` + `chats: [ChatRow]` |
| PATCH | `/clients/{id}` | `{name?, phone?, notes?, timezone?, ai_enabled?, memory?}` → `Client` |

```ts
Client = {id, name, username, phone, timezone, notes, ai_enabled,
          memory: [{fact, event_id, at}], created_at, last_seen_at}
```

## Живые обновления

`GET /api/events` — Server-Sent Events. Каждое событие журнала:
`data: {"chat_id": 1, "event_id": 42, "kind": "message_in"}`. Фронт по нему перечитывает
ленту и список чатов.

## Служебное

`GET /healthz` — процесс жив. `GET /readyz` — есть связь с БД.
