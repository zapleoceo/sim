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
| GET | `/chats?q=&limit=50&before=<last_message_at>&persona_id=` | → `{items: [ChatRow]}`; `persona_id` — только чаты этого персонажа |
| GET | `/chats/{id}` | → `ChatRow` + `client: Client` |
| GET | `/chats/{id}/timeline?before_id=&limit=50` | → `{items: [TimelineItem]}` от новых к старым |
| POST | `/chats/{id}/messages` | `{text}` или multipart (`file`, `caption`) — пишет оператор от имени персонажа → `TimelineItem` |
| POST | `/chats/{id}/ai` | `{enabled}` → `ChatRow` |
| POST | `/chats/{id}/read` | → 204 (обнуляет `unread_count`) |
| POST | `/chats/{id}/notes` | `{text}` — заметка для агента (`operator_note`) → `TimelineItem` |
| POST | `/chats/{id}/operator-done` | снимает «нужен оператор» → `ChatRow` |
| GET | `/reply-runs/{id}` | → `{id, status, context, plan, guard_verdicts, error, llm_calls: [LlmCall]}` |

```ts
ChatRow = {id, client: {id, name, username, ai_enabled}, persona: PersonaBrief | null,
           ai_enabled, needs_operator, unread_count, last_message_at,
           last_message: {kind, direction, author, text} | null}
PersonaBrief = {id, name, avatar_media_id}     // персонаж, который отвечает в этом чате
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

## Персонажи

Персонажей может быть несколько ([personas.md](personas.md)). Всё, что относится к одному
персонажу, лежит под `/personas/{id}/…`; старых путей `/persona…` и `/channels` больше нет.

| Метод | Путь | |
|---|---|---|
| GET | `/personas` | → `{items: [PersonaRow]}`: активные, потом архивные |
| GET | `/personas/{id}` | → `Persona`; 404 — нет такого |
| PUT | `/personas/{id}` | частичное обновление полей `Persona` (кроме `is_active`) → `Persona`; имя уникально (400 «Персонаж с таким именем уже есть»); аватар — только из её медиатеки; агенты и запреты Охранника здесь не меняются (неизвестные поля игнорируются) |
| PATCH | `/personas/{id}` | `{is_active}` — в архив (`false`) или обратно → `Persona` |
| POST | `/personas/{id}/clone` | `{name, parts: [ClonePart]}` → новый `Persona` (201); 400 — имя занято или пустое, неизвестная часть |
| POST | `/personas/{id}/appearance/describe` | агент Портретист (по умолчанию модель Зрения) описывает внешность по фото-референсам → `{text, references}`; ничего не сохраняет (текст правят и сохраняют через `PUT /personas/{id}` `{appearance}`); 400 — нет модели или референсов |
| GET | `/models` | → `{items: [{id, available, input_modalities, output_modalities, capabilities}]}` из Broker |
| GET | `/voices` | → `{items: [{id, label}]}` голоса Gemini TTS |

```ts
PersonaRow = {id, name, avatar_media_id, is_active, ai_enabled, leads_count, channels: [Channel]}
ClonePart = "basics"|"prompt"|"agents"|"voice"|"guard"|"appearance"|"world"|"arcs"|"media"
          |"switches"|"canon"
```

`Persona` — поля таблицы `persona` (`docs/database.md`), кроме служебных, `life` и `agents`
(свои пути ниже) и `guard_rules` (в «Агентах»); в том числе `appearance`,
`voice_ambience_enabled`, `is_active`.

## Агенты персонажа

Каждая функция ИИ внутри персонажа — агент ([agents.md](agents.md)): модель, уровень ключей,
промт, max_tokens.

| Метод | Путь | |
|---|---|---|
| GET | `/agents/registry` | → `AgentRegistry`: группы, агенты, значения по умолчанию, промты, фильтры моделей |
| GET | `/personas/{id}/agents` | → `PersonaAgents`; 404 — нет персонажа |
| PUT | `/personas/{id}/agents` | `{agents: {key: AgentIn}, guard_rules?}` → `PersonaAgents`. Присланные агенты заменяют свои настройки целиком, остальные не трогаются. 400: неизвестный агент, неизвестный `key_tier`, `max_tokens` вне 64…16384 или у агента без лимита, промт у агента без промта, лишнее поле, модель, которой нет в читаемом непустом каталоге Broker (проверяются только изменённые модели; недоступная сейчас модель сохраняется) |
| POST | `/personas/{id}/agents/{key}/reset` | всё у агента по умолчанию → `PersonaAgents`; 404 — неизвестный агент |

```ts
KeyTier = "auto"|"paid"|"free"
ModelMatch = {input: string[], output: string[], capabilities: string[]}   // все из списка
AgentSpec = {key, title, group, description, port, model_filter: [ModelMatch] /* любой */,
             key_tier, model, fallback: string|null, prompt: string|null,
             readonly_prompt: string|null, placeholders: string[], max_tokens: number|null}
AgentRegistry = {groups: [{key, title, description, agents: [key]}], agents: [AgentSpec],
                 key_tiers: [{value: KeyTier, label}], max_tokens_range: {min, max}}
AgentIn = {model?: string|null /* null — по умолчанию, "" — не использовать */,
           key_tier?: KeyTier|null, prompt?: string /* "" — по умолчанию */,
           max_tokens?: number|null}
PersonaAgents = {agents: {key: {model: string|null, key_tier: KeyTier, prompt: string,
                                max_tokens: number|null, effective_model: string}},
                 guard_rules: string}
```

`effective_model` — модель, которой агент реально пойдёт (по умолчанию или модель
агента-запасного); `""` — агент не работает. Сохраняется только отличие от умолчания: уровень
ключей и max_tokens, равные стандартным, и промт, равный стандартному, не хранятся.

## Канал персонажа

Подключение аккаунта Telegram по QR — [personas.md](personas.md), п. 3. Ответы никогда не
содержат api_hash, сессию и пароль.

| Метод | Путь | |
|---|---|---|
| GET | `/personas/{id}/channels` | → `{items: [Channel]}` |
| POST | `/personas/{id}/channels/telegram/login` | `{api_id, api_hash, proxy?}` → `LoginStep`; канал персонажа создаётся при первом входе |
| GET | `/personas/{id}/channels/{cid}/login` | → `LoginStep` (дашборд спрашивает раз в 2 с, пока `state = qr`) |
| POST | `/personas/{id}/channels/{cid}/login/password` | `{password}` — облачный пароль 2FA → `LoginStep` |
| DELETE | `/personas/{id}/channels/{cid}/login` | отменить вход → 204 |
| POST | `/personas/{id}/channels/{cid}/disable` | «Отключить»: статус `disabled` → `Channel` |

```ts
Channel = {id, persona_id, kind, title, status: "new"|"connected"|"error"|"disabled",
           account_label, last_error, last_activity_at}
LoginStep = {channel: Channel,
             login: {state: "qr"|"password"|"connected"|"error"|"none", message,
                     qr: string | null /* SVG data URI */}}
```

## Жизнь персонажа

Логика — [persona-life.md](persona-life.md). Все пути — под `/personas/{id}`.

| Метод | Путь | |
|---|---|---|
| GET | `/personas/{id}/life` | → `PersonaLife` |
| PUT | `/personas/{id}/life` | `PersonaLife` целиком → `PersonaLife`; 400 «Неверные данные: …» |
| GET | `/personas/{id}/arcs` | → `{items: [Arc]}` |
| POST | `/personas/{id}/arcs` | `{title, summary?, stage?, status?, started_on?, ends_on?}` → `Arc` (201; `started_on` по умолчанию — её сегодня) |
| PATCH | `/personas/{id}/arcs/{arc}` | любые поля `Arc` → `Arc` |
| DELETE | `/personas/{id}/arcs/{arc}` | → 204 |
| GET | `/personas/{id}/days?from=&to=` | → `{today, now_minute, items: [Day]}`; по умолчанию вчера…завтра по её календарю, не больше 62 дней |
| PUT | `/personas/{id}/days/{YYYY-MM-DD}` | `{events: [DayEvent], summary?}` → `Day` (`source = edited`; 400 — время не по порядку, наложения) |
| POST | `/personas/{id}/days/{YYYY-MM-DD}/plan` | спланировать заново (задача `agent.plan_day`, `force`) → 202 `{queued, day}`; 400 — Планировщик выключен (у агента `life` нет модели) |
| GET | `/personas/{id}/canon?status=active\|rejected` | → `{items: [Canon]}` от новых к старым |
| PATCH | `/personas/{id}/canon/{canon}` | `{fact?, status?}` → `Canon` |

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
| GET | `/personas/{id}/media?source=library\|generated\|incoming&kind=photo\|video` | → `{items: [Media]}`; по умолчанию медиатека и сгенерированное этого персонажа; `incoming` — вложения клиентов из её чатов |
| POST | `/personas/{id}/media` | multipart: `file`, `caption`, `tags` (через запятую), `is_reference` → `Media` (201) |
| PATCH | `/media/{id}` | `{caption?, tags?, is_reference?}` → `Media` (id файла глобальный) |
| DELETE | `/media/{id}` | → 204 |
| GET | `/media/{id}/file` | содержимое файла (только для вошедших) |

## Лиды (клиенты)

Логика — [leads.md](leads.md).

| Метод | Путь | |
|---|---|---|
| GET | `/clients?q=&persona_id=&stage=` | → `{items: [Lead]}`: последний контакт сначала, до 1000; `stage` — стадия паспорта (пустой паспорт = `new`); 400 — неизвестная стадия |
| GET | `/clients/{id}` | → `Client` + `persona: PersonaBrief` + `chats: [ChatRow]` |
| PATCH | `/clients/{id}` | `{name?, phone?, notes?, timezone?, ai_enabled?, passport?: PassportPatch}` → `Client` |

```ts
Client = {id, persona_id, name, username, phone, timezone, notes, ai_enabled,
          passport: Passport, created_at, last_seen_at}
Lead = Client & {persona: PersonaBrief | null, chats_count, messages_count}
Passport = {text, name, age: number | null, gender, city, country, timezone, occupation,
            stage: "new"|"acquainted"|"interested"|"warm"|"hot"|"client"|"lost",
            temperature /* 0–100 */, next_step, updated_at, locked_fields: string[]}
PassportPatch = любые поля Passport, кроме updated_at; каждое отправленное структурное поле
                попадает в locked_fields; locked_fields в запросе заменяет список (снять замок)
```

`timezone` клиента в профиле оставлен для совместимости: часовой пояс клиента правится в
паспорте (`passport.timezone`), он и считается в фактах.

## Живые обновления

`GET /api/events` — Server-Sent Events. Каждое событие журнала:
`data: {"chat_id": 1, "event_id": 42, "kind": "message_in"}`. Фронт по нему перечитывает
ленту и список чатов.

## Служебное

`GET /healthz` — процесс жив. `GET /readyz` — есть связь с БД.
