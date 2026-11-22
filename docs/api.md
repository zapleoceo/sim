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
| GET | `/chats/{id}/status` | → `PersonaStatus` — «Статус Марины» этого чата сейчас (только для команды, [attention.md](attention.md), «Статус для команды») |
| GET | `/chats/{id}/timeline?before_id=&limit=50` | → `{items: [TimelineItem]}` от новых к старым |
| POST | `/chats/{id}/messages` | `{text}` или multipart (`file`, `caption`) — пишет оператор от имени персонажа → `TimelineItem` |
| POST | `/chats/{id}/ai` | `{enabled}` → `ChatRow` |
| POST | `/chats/{id}/attention-instant` | `{enabled}` — «Отвечать сразу» в этом чате (без проверок телефона, [attention.md](attention.md)) → `ChatRow` |
| POST | `/chats/{id}/no-refusals` | `{enabled}` — «Без отказов» в этом чате ([writing.md](writing.md)) → `ChatRow` |
| POST | `/chats/{id}/calls-out` | `{enabled}` — «Звонит сама» в этом чате ([initiative.md](initiative.md)) → `ChatRow` |
| GET | `/chats/{id}/contacts` | → `{items: [Contact]}` — запланированные сообщения и звонки, новые сверху ([initiative.md](initiative.md)) |
| POST | `/chats/{id}/contacts` | `{kind: message|call, topic, due_at}` → `Contact` (201); 400 — время прошло, нет темы, звонки недоступны |
| POST | `/chats/{id}/contacts/{cid}/cancel` | → `Contact`; 409 — уже начался |
| POST | `/chats/{id}/read` | → 204 (обнуляет `unread_count`) |
| POST | `/chats/{id}/notes` | `{text}` — заметка для агента (`operator_note`) → `TimelineItem` |
| POST | `/chats/{id}/operator-done` | снимает «нужен оператор»: событие `ai_toggled` `{enabled: true, scope: "operator"}` и, если клиент писал, пока ждали оператора, — проверка телефона персонажа, как после включения ИИ ([reply.md](reply.md)); повторно — ничего → `ChatRow` |
| GET | `/reply-runs/{id}` | → `{id, status, context, plan, guard_verdicts, error, llm_calls: [LlmCall]}` |

```ts
ChatRow = {id, client: {id, name, username, ai_enabled}, persona: PersonaBrief | null,
           channel: {id, kind, title} | null,  // мессенджер чата: значок в списке и шапке
           ai_enabled, attention_instant, no_refusals, needs_operator, unread_count, last_message_at,
           last_message: {kind, direction, author, text} | null,
           persona_status: PersonaStatus}
PersonaBrief = {id, name, avatar_media_id}     // персонаж, который отвечает в этом чате
PersonaStatus = {code, tone: "ok" | "wait" | "busy" | "warn" | "off",
                 title,               // «Прочитала, ответит около 20:40»
                 detail,              // причина / пояснение, может быть ""
                 until: UTC | null,   // когда пауза кончится, если известно
                 local_until: "ЧЧ:ММ" | null}    // то же по её часовому поясу
// Считается при чтении, нигде не хранится, клиенту и агентам не уходит. Статусы страницы
// списка — фиксированным числом запросов, её доступность — один раз на персонажа.
TimelineItem = {id, kind, actor, created_at, in_context, reply_run_id,
                message?: {id, direction, author, kind, text, status, error, sent_at,
                           media?: Media},
                call?: {id, direction, status, duration_s, transcript: [{who, text, t}]},
                generation?: {id, kind, status, caption, error},
                data: object}
Media = {id, kind, mime, url, caption, tags, description, source, is_reference,
         width, height, duration_s, file_name, created_at,
         scene?: GenerationScene | null}      // url = /api/media/{id}/file; scene — только в медиатеке
GenerationScene = {day?, place?, activity?, outfit?, light?, shot?, camera_reason?, room?,
                   room_ref?: bool}  // `generation.scene` сгенерированного файла; null — загруженный
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
| GET | `/calls/{id}/recording` | → OGG записи разговора (Range); 404 — записи нет или удалена ([calls.md](calls.md)) |
| POST | `/personas/{id}/call-recordings/purge` | `{days: int|null}` — удалить записи звонков персонажа старше `days` (`null` — её срок, `0` — все) → `{deleted}` |
| GET | `/voices` | → `{items: [{id, label, gender: female|male, tone}]}` голоса Gemini TTS |
| GET | `/voices/{id}/sample?persona_id=` | → WAV: короткая фраза этим голосом моделью агента «Голос» персонажа; кэш в процессе (каждый голос — один вызов TTS); 404 — нет голоса, 400 — нет модели, 502 — Broker отказал |

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
| PUT | `/personas/{id}/agents` | `{agents: {key: AgentIn}, guard_rules?, attention?: Attention}` → `PersonaAgents`. Присланные агенты заменяют свои настройки целиком, остальные не трогаются; `attention` (все поля) заменяет настройки Внимания. 400: неизвестный агент, `attention` не по схеме (режим, темп вне 0.5…2, нет поля, лишнее поле), неизвестный `key_tier`, `max_tokens` вне 64…16384 или у агента без лимита, промт у агента без промта, лишнее поле, модель, которой нет в читаемом непустом каталоге Broker (проверяются только изменённые модели; недоступная сейчас модель сохраняется) |
| POST | `/personas/{id}/agents/{key}/reset` | всё у агента по умолчанию (у `attention` — и его настройки) → `PersonaAgents`; 404 — неизвестный агент |

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
                 guard_rules: string, attention: Attention}
Attention = {mode: "human"|"free_only"|"instant", pace: number /* 0.5…2 */,
             may_leave_read: boolean, after_missed_call: boolean, night_dnd: boolean}
                 // docs/attention.md; GET всегда отдаёт все поля (по умолчанию — human, 1, да, да, да)
```

`effective_model` — модель, которой агент реально пойдёт (по умолчанию или модель
агента-запасного); `""` — агент не работает. Сохраняется только отличие от умолчания: уровень
ключей и max_tokens, равные стандартным, и промт, равный стандартному, не хранятся.

## Каналы персонажа

Telegram (вход по QR, [personas.md](personas.md), п. 3), Instagram Direct и WhatsApp
([channels.md](channels.md)). У персонажа по одному каналу каждого вида; канал создаётся при
первом входе. Ответы никогда не содержат api_hash, сессии, пароли, коды, sessionid, JSON сессии
и секрет вебхука. Код: `sim/api/routes/channels.py`, потоки входа — `sim/api/channel_logins.py`.

| Метод | Путь | |
|---|---|---|
| GET | `/personas/{id}/channels` | → `{items: [Channel], kinds: [ChannelKindInfo]}` |
| POST | `/personas/{id}/channels/telegram/login` | `{api_id, api_hash, proxy?}` → `LoginStep` |
| POST | `/personas/{id}/channels/instagram/login` | ровно один способ: `{username, password, proxy}`, или `{sessionid, proxy}`, или `{settings_json, proxy}`; прокси обязателен (или `SIM_IG_DEFAULT_PROXY`); лишнее поле → 400 → `LoginStep` |
| POST | `/personas/{id}/channels/whatsapp/login` | `{phone?}` (только подпись) → `LoginStep` с QR; канал сразу `pending` и хранит инстанс и секрет вебхука |
| GET | `/personas/{id}/channels/{cid}/login` | → `LoginStep`; дашборд спрашивает раз в 2 с, пока `state` = `qr` или `device` (Instagram повторяет вход сам) |
| POST | `/personas/{id}/channels/{cid}/login/code` | `{code}` — код 2FA / подтверждения Instagram; пустой — «Повторить» / «Я подтвердил» → `LoginStep` |
| POST | `/personas/{id}/channels/{cid}/login/password` | `{password}` — облачный пароль 2FA Telegram → `LoginStep` |
| DELETE | `/personas/{id}/channels/{cid}/login` | отменить вход → 204; незавершённая привязка WhatsApp ещё и отвязывается в Evolution (logout + delete), канал снова `new` |
| POST | `/personas/{id}/channels/{cid}/disable` | «Отключить»: статус `disabled` → `Channel` |

```ts
Channel = {id, persona_id, kind: "telegram_user" | "instagram" | "whatsapp", title,
           status: "new" | "connected" | "error" | "disabled"
                 | "pending"     // идёт вход: показан QR, ждём код
                 | "challenge",  // мессенджер требует войти заново («нужен повторный вход»)
           account_label, last_error, last_activity_at}
ChannelKindInfo = {kind, label, available, reason}   // reason: «Сервис WhatsApp не запущен на сервере»
LoginStep = {channel: Channel,
             login: {state: "none" | "qr" | "password" | "code" | "device" | "challenge"
                          | "manual" | "connected" | "error",
                     message,
                     qr: string | null,      // data URI QR (Telegram, WhatsApp)
                     detail: string | null}} // code: totp | sms; challenge: email | sms
```

Статус канала во время входа: начатый вход переводит `new` / `error` / `challenge` в `pending`;
ошибка или «вход устарел» возвращают `pending` в `new`; рабочий (`connected`) и выключенный
канал вход не трогает, пока он не завершится.

## Вебхуки

`POST /api/hooks/evolution/{channel_id}` — события WhatsApp от Evolution API
([channels.md](channels.md), «Приём»). Без входа в дашборд и без CSRF (`opt public`), но с
заголовком `x-sim-webhook-secret`, равным секрету этого канала (хранится зашифрованным,
сравнение за постоянное время): нет такого WhatsApp-канала → 404, секрет не тот → 403.
Выключенный канал → 200 и ничего. Иначе → `{ok: true, stored: N}` — сколько новых сообщений
клиентов принято. Тело не логируется.

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
               hobbies: [{name, details, frequency}], places: [{name, kind, details}],
               look: {nails, earrings, jewelry, hair, other},
               home: {details, rooms: [{name, details, layout, lamps, window_view}]}}
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
| PATCH | `/media/{id}` | `{caption?, tags?, is_reference?, is_room_ref?}` → `Media` (id файла глобальный). `is_reference` — референс лица: только фото (загруженное или сгенерированное), иначе 400. `is_room_ref` — эталон комнаты: только сгенерированное фото, снятое в комнате её квартиры (`scene.room`), иначе 400 «Эталоном комнаты может быть только…»; пишется в `generation.scene.room_ref` |
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

## Дни (память чата)

Логика — [memory.md](memory.md). Вкладка «Дни» карточки лида.

| Метод | Путь | |
|---|---|---|
| GET | `/chats/{id}/days?limit=60` | → `{today, items: [ChatDay], weeks: [ChatWeek]}`: новые сначала, `limit` 1–120 дней, до 12 недель; `today` — её местная дата |
| PUT | `/chats/{id}/days/{YYYY-MM-DD}` | `{summary, facts?}` — правка владельца → `ChatDay` с `source: "edited"` (ночная задача её не перезаписывает); пустой итог или лишнее поле — 400 |
| POST | `/chats/{id}/days/{YYYY-MM-DD}/rebuild` | «Пересобрать день» → 202 `{queued, day}` (задача `agent.summarize_day(force)`: итог заново, потом паспорт); 400 — Летописец выключен или день ещё не закончился |
| POST | `/chats/{id}/passport/refresh` | «Обновить паспорт сейчас» → 202 `{queued}` (задача `agent.update_passport` по сегодняшнему дню); 400 — Паспортистка выключена |

```ts
ChatDay = {day: "YYYY-MM-DD", summary, facts: string[], source: "model" | "edited", model,
           error: string | null, first_event_id, last_event_id, updated_at}
ChatWeek = ChatDay без day + {week: "YYYY-MM-DD" /* понедельник */}
```

`reply-runs/{id}.context.days` — какие итоги дней были в контексте хода (`[{day, summary,
source}]`), `context.recall` — что персонаж вспоминал («А помнишь…?»).

## Живые обновления

`GET /api/events` — Server-Sent Events. Каждое событие журнала:
`data: {"chat_id": 1, "event_id": 42, "kind": "message_in"}`. Фронт по нему перечитывает
ленту и список чатов. Поток не отдаёт SSE `id:` и не продолжает с `Last-Event-ID`: события
за время обрыва не досылаются, поэтому после переподключения фронт сам перечитывает списки
чатов, открытый чат, ленты и клиентов (docs/frontend.md, «Живые обновления»).

## Служебное

`GET /healthz` — процесс жив. `GET /readyz` — есть связь с БД.
