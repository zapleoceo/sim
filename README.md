# Sim

### Live call startup retries

While an incoming call is still ringing, SIM retries Broker Live startup at most once,
and only for an explicitly classified connection timeout or connection refusal before
it attempts to send `start`. Authorization, quota and budget refusals, unclassified
connection failures, and ambiguous send or ready outcomes are not retried. This avoids
duplicate starts; it does not prove a remote session has stopped or restore provider
capacity. See the [live session retry boundary and CI coverage](docs/live-open-retry-boundary.md).

### Bounded video-note normalization

The existing MP4-to-Telegram-circle encoder now rejects oversized/invalid inputs,
uses the resource-contained child runner with bounded output, and verifies H.264/AAC,
square dimensions and retained audio. AI disclosure stays visible. Synthetic CI covers
normalization, malformed media, overflow and cancellation. This does not activate Studio
or add provider/channel calls. See [limits and remaining gates](docs/media-routing.md).

### Dormant Studio media routes

Studio shows a collapsed route plan with explicit blockers, not a live Broker check.
Owner preferences for subscription photos, Google photo reserve and regular video persist
through the existing settings API; defaults are empty and cannot activate generation.
Unknown outcomes, budgets, safety and permission denials never permit fallback.
See [media routing contracts and provider limits](docs/media-routing.md).
Synthetic CI covers persistence/reset, auth/CSRF, route validation and unavailable UI.

### Expired media generation leases

An expired, already-claimed running `video_note` or `persona_media` job is quarantined
with terminal `media_generation_outcome_unknown` rather than re-dispatched. The
original payload, attempts and any stored receipt are retained for reconciliation.
A still-locked live handler can finish; existing pending jobs and unrelated work
keep their current policy. A crash before submitting can also be quarantined,
since reclaim cannot distinguish it from a lost accepted response. Do not create
replacement jobs solely because no remote ID is known.

This is not general logical idempotency and does not activate generation, new
providers or outbound media. A future route must commit a durable intent before one
POST, checkpoint its remote ID before polling, and reconcile unknown outcomes without
a second POST or SEND. CI exercises the PostgreSQL race with mocked effects only.

PR124's PostgreSQL gate also forces revision-recovery and a competing busy reply
to overlap at the missing pending-job read. It asserts one pending replacement and
the later busy deadline; this is separate from the transcript/worker-instance regression.
Test-only SHA 649bf6c / CI 44057567711 reproduced exactly two pending retries in
PostgreSQL; unit tests and Ruff passed before the concurrency assertion failed.

Reply pending writers now serialize through the existing NS_DIALOG lock in JobRepo,
including direct enqueue, upsert, scheduling reads/edits, cancellation and retry settlement.
NS_REPLY is only tried; claim uses SKIP LOCKED without taking NS_DIALOG.
Pure deadline observers opt out of locks, so the overdue-watch batch cannot accumulate
dialog locks in a conflicting order.
Failure settlement must skip a locked live running row rather than wait for it under NS_DIALOG. Pending reads
lock and refresh their row so a concurrent claim cannot turn a stale cached row into a lost retry.
This guarantee covers canonical reply:<dialog_id> keys, not all unrelated job types or raw SQL.
Fresh pending retries coalesce into the original executing job ID, retaining its Broker journal,
latest payload and later deadline. Two already-executed retry identities fail closed with
reconciliation diagnostics and the existing giveup policy; neither is blindly replayed.
Before rollout, inspect historical duplicate pending reply keys; do not silently delete or
merge previously executed jobs with unknown external receipts. No migration rewrites history.

### Settings and helper editors

Shared settings and helpers use a wide grouped workspace: desktop group navigation,
mobile section selector, explicit instruction editors, concise helper roles and current
model controls. Existing setting/group hashes (including Back/Forward) and owner access
are retained. Fixed guard/honesty text remains available in read-only details.
Save actions appear only for changed fields; reset requires confirmation. Drafts live
only in the current query client's memory, survive SPA navigation and refetch, and are
not written to browser storage. An app-level unload guard warns while any settings draft
exists, including after navigating to Inbox or a persona. Saving/discarding the last
draft removes the listener. Closing/reloading despite the warning loses memory drafts;
browser unload prompts are best-effort, not persistent storage. A changed server value is shown as a conflict; overwriting it needs
explicit confirmation, or the owner can load the saved value. This is UI conflict
awareness, not a server-side atomic compare-and-swap. Failed mutations retain the draft.
In-flight field writes stay disabled even after navigating away and back, so an older
save cannot erase newer input entered through a remounted editor.
CI component coverage uses fake APIs for save/reset/CSRF, route/hash history, refetch
conflicts, errors and permissions. No model routes are activated by this UI change.
Live desktop/mobile visual acceptance has not been performed.

### Owner-selected conversation models

Personas keep the existing primary model and add two empty-by-default choices:
`identity_model` (otherwise primary) and `fallback_model` (otherwise off). The identity
selector applies to text conversation drafts/previews, including reviewed repairs.
A conservative deterministic multilingual detector recognizes direct identity questions
and short follow-ups; quotes and third-party questions remain on primary. It is a routing
hint, not a disclosure policy: every model keeps the same mandatory honesty, financial
boundaries and fresh guard. A fixed identity boundary follows editable generator
instructions and format; the guard has the same boundary and must reject false human
identity claims independently of the detector and model selection. These fixed layers
are visible in settings/context and cannot be disabled by a persona or custom guard prompt.
Ordinary conversation need not repeatedly mention AI. Live-call model
selection is unchanged. Detection is deliberately not a claim of perfect language coverage.

Reserve is one owner-selected **subscription** model, checked against the same project's
catalog before use. Only confirmed terminal Broker `error_code=model_unavailable` or
`capacity_busy` permits it. Timeout/pending/unknown POST, quota/budget/billing/auth failures,
cancellation, safety and unclassified failures do not permit fallback. The adapter still
reconciles all unresolved requests before any POST. Routed calls preserve terminal receipts
across worker generations: a restart after reserve acceptance does not re-buy primary or
reserve. Terminal failures remain recorded for that logical request, rather than retried
under a new key on every worker attempt. Changing effective context/config creates a new
logical request under the PR117 reconciliation rules. Broker remains the authorization
and budget authority; no key, scope, quota or provider changes are made.

Source contract: Broker server checkout `df4460a5bbe3a3e7311861e024e1ca1b1c893156`,
`agent/job_status.py` stable error codes and `routes/proxy.py:764-786` poll serialization.
This is source inspection, not a paid/live provider probe. CI uses synthetic multilingual
fixtures, fake catalog/transports, API roundtrips and durable reserve restart regression.
Conflicting persona/format/guard fixtures cover primary, identity, subscription reserve,
detector misses, repaired replies and a repair that remains blocked. These tests verify
the assembled controls and guarded pipeline with scripted verdicts, not live model compliance.
Historical private identity questions are not published as fixtures. Financial budget
exhaustion is intentionally not automatically bypassed; no reserve is silently selected.

### Durable Broker chat recovery

Worker chat calls commit an encrypted request and random project-scoped idempotency
key before POST `/v1/jobs`, then checkpoint the remote job ID and terminal result.
After a lost POST response, a read-only lookup by its unique label and idempotency
key recovers the accepted job; after acceptance, only that job is polled. No unknown
POST is replayed: Broker retention deletes terminal jobs and their keys, so a missing
receipt (including a crash before POST) requires reconciliation, never blind resubmission.
All unresolved calls of the worker job are reconciled before
any new request, changed context or future fallback can be submitted. A confirmed
terminal failure is distinct from an unknown result; only the former permits a new
attempt in a later worker generation. Completed identical calls reuse their saved
result. Conversation generation identifies the logical operation with a canonical
clock used only for hashing, plus dialog activity revision and current persona/config
context. A 90-second restart alone reuses the accepted generator result; new inbound
messages or changed instructions/settings invalidate it. The actual submitted prompt
keeps real time, and guard always checks the recovered draft against freshly computed
trusted facts. A rejected stale draft is repaired through the normal guarded path.
No canonical clock is sent to a model. Non-conversation calls retain exact-body identity.
Reuse assumes unchanged effective context: history-window expiry, expiry of a recent answered-call fact, or media cooldown expiry may intentionally require a fresh generation
after prior requests settle. This does not guarantee zero additional generation cost
at these boundaries; uncertain requests still block resubmission. See the contract
document for this distinction and the real-handler audit-only incident regression.
Profile snapshots provide a separate identity representation without their observation
timestamp; actual prompts retain that timestamp. Status, fields, user-authored dates
and photo bytes are preserved in the identity, so genuine profile changes invalidate it.
Recovered results do not book the original cost/tokens again in the per-attempt audit;
the original usage stays in the saved Broker response. Broker usage remains the
billing authority, including when a process died before its first audit was saved.

The journal has no FK to the locked worker Job, uses independent short commits,
checks the live execution transaction before effects, and fences checkpoints by
generation plus request key. Private request/results use the existing SecretBox;
they are not exposed in API/logs. Per-record plaintext limits are 2 MB request and
1 MB response; excess fails closed. Broker URL/project-key changes require explicit
reconciliation rather than replay under different authorization. A nonempty journal
blocks downgrade so a rollback cannot erase duplicate-prevention history.

This increment covers asynchronous worker **chat** requests only. Interactive calls,
speech/image/video endpoints, orphaned requests from pre-journal releases and automatic
fallback selection are not covered. It requires the Broker's existing project-scoped
Idempotency-Key and project-scoped label-listing contracts; a conflict, missing remote job or unknown response never
switches keys/models automatically. CI uses synthetic HTTP and temporary PostgreSQL,
including real connection termination during POST/poll and late-owner fencing. No
live provider probes or client sends are used. Existing channel send receipts remain
the separate authority for delivery; a completed model call does not prove delivery.
HTTP 400/401/402/403/422 alone is NOT evidence of non-acceptance (replay can be denied
after an earlier acceptance). Such an intent remains unresolved until read-only
reconciliation succeeds. See `docs/broker-recovery-contract.md` for source evidence.

An unanswered reply whose Broker intent remains unresolved when worker retries expire
stays failed for reconciliation; the trace shows `broker_reconciliation_required`.
The worker does not auto-pause the chat, hand it to an operator, or blindly replay
that request. Operator pauses, opt-outs and guard handoffs remain unchanged.

### Recorded agents and selected activity calls

The owner-only log and trace drawer show role/workflow, saved model and provider from
the stored LLM call, not current persona settings or the worker actor. Historical
model values may be the requested model (including failed calls), not a confirmed
responding model; the drawer explains this limitation without inventing provenance. Missing history
is labelled explicitly. The log row's step or LLM card's call ID travels with its trace
in the URL, preserving selection through Back/Forward. The selected record appears
first with the existing redacted request/response blocks expanded; remaining steps,
errors, truncation notices and copy controls remain available. No payload is rebuilt.
Tests use synthetic API fixtures; live browser visual acceptance remains separate.

### Live call audio backlogs

The Telegram input queue retains the latest 50 audio frames (typically 500 ms),
rejects empty or oversized frames and reserves space for hang-up. The native callback
coalesces incoming mic PCM through one pending loop drain and up to 128 frames per
bridge, not a coroutine per native batch. Its pinned single NTgCalls frame callback
is voice-only: SIM has no other frame consumers and ignores non-mic/non-incoming
frames as before. ChatStatus and other native callbacks are unchanged. Video would
need an explicit second bounded route, not this mailbox. SIM dispatch starts only
after native startup and mailbox installation both succeed. If the Broker
stream exceeds 128 pending events or 512 KiB of pending payload, SIM discards the
undelivered backlog and ends the stream as a technical failure. It never drops
individual model events and continues, because playback guard needs the matching
audio, transcript and interruption sequence. Each WebSocket message is limited to
1 MiB and negotiated PCM rates to 8–48 kHz. The existing guarded playback and
delivery-receipt rules are unchanged. CI uses fake streams only.

### Model context excludes internal audit and rejected drafts

The communication feed and terminal handoff audit are unchanged. Model context
keeps only allowlisted conversation facts from journal events, with no raw actor
or reason metadata. A recent answered call may contribute only the neutral fact
that some speech may not have played; unconnected attempts and causes stay in
audit. Actual delivered conversation remains intact. Known system
boilerplate is projected out of derived passport/summary text without rewriting
stored data. Standalone internal next-step assignments to transfer or pause AI
are omitted at read time; mixed steps and participant quotes stay intact. A rejected reply is regenerated from clean context and a bounded
repair category, not a previous unapproved draft or free-form guard explanation.
The new draft is checked again. Superseded attempts still send nothing, and their
worker detail now distinguishes a revision change from a newer inbound ID.

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

### Prepared Telegram MP4 transport

Telegram user accounts expose a dormant port for one prepared MP4. It checks
size and bounded MP4 container structure before resolving the exact private-user
entity. It uses one throttled upload invocation with a literal caption and
requires a positive message ID as transport acknowledgement. Durable SendReceipts
remain separate. This is structural validation,
not codec or playback verification. The adapter does not replay uncertain
uploads or replace video with a text send; it does not change Telethon's own
transport handling.

Ordinary gallery video still cannot be delivered by SIM. This port is not wired to
outbound, gallery selection, Studio or generation.
A future caller must resolve owner-uploaded PersonaMedia for the correct persona
through the local MediaStore, enforce recipient consent and existing pause/
refusal guards, obtain SendReceipts before mutation, and fail closed on missing
or unsupported video rather than using the ordinary text fallback. Any future
paid generation also needs durable pre-submit intent and remote-ID reconciliation.
CI uses only fake Telegram clients and synthetic MP4 boxes.

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

### Telegram voice bridge attachment

A transient timeout or connection failure during the read-only bridge preflight
(get the logged-in Telegram user, resolve that user's own peer) does not erase the
handler. Messaging remains available; the following eligible use after a 30-second
cooldown retries the preflight. Successful attachment does not make recurring probes.
The preflight timeout is 5 seconds. Authorization and unknown failures do not retry.

A failure or cancellation after native construction starts is different: the binding
may already have registered callbacks, and removing Telethon subscriptions only
is not full cleanup. The client will not retry native startup in that case; reset
the transport/worker after diagnosis. SIM subscribes to call events only after native
startup returns successfully. CI uses fake clients, not real Telegram calls.

### Reply queue and log display

The dialog API derives a read-only phase from the actual selected queue row and
server time: queued, writing with a current lease, or preparing/awaiting recovery
when the running lease is missing or expired. An expired lease does not prove a
worker stopped; the live execution lock can still hold. The UI no longer treats
this uncertain phase as writing. Manual Reply Now behavior and worker leases,
deadlines, retry policy and delivery are unchanged. In Activity, successful
worker steps report deferred, superseded, skipped or handed-off replies without
changing stored status or filters.

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

В конце каждой голосовой реплики SIM дополняет тишиной неполный 10-мс кадр.
Когда Telegram-транспорт принял последний кадр, следующая реплика может пройти
проверку и прозвучать. Это применяется и к короткому приветствию.

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

### Incoming subscription media (opt-in)

The bounded recognition component and its current format/capability limits are documented in
[Incoming media](docs/incoming-media.md). Queue integration defaults off, with explicit
pending/done/unsupported/failed states, bounded Telegram downloads, retained captions and
durable Broker reconciliation. Video is sampled frames only, not speech recognition;
audio/PDF/Office are unsupported in this mode. No paid fallback. Synthetic checks run only in CI.
# Studio media wiring (disabled)

The media/reply concurrency regression runs early in CI and then with the full suite.
It deterministically commits a synthetic transcription between reply's initial dialog read
and its media-job check, as well as testing the opposite order. A superseded draft must
never be sent and must not strand an unanswered message when the worker restarts.
CI44047747901 at test-only521a1cd proved this loss (superseded, no replacement), while
the opposite order passed. Revision changes now retain one normal deduplicated reply;
existing later schedules remain later, and pause/refusal checks still take precedence.
No stale draft is delivered or reused, no proactive follow-up is automatically retried,
and the regular model/guard/budget/send-receipt path is unchanged. Synthetic regressions
cover newer input, delayed pending work, worker recreation, pause/resume and refusal.
Resume fixtures explicitly distinguish fresh messages from expired recovery windows;
expired messages remain unanswered. The legacy media fixture supplies the same synthetic
answer for a valid regenerated draft without relaxing its expected delivered text.
This proves the race itself, not the historical cause of the earlier intermittent CI failure.

Private video drafts can be saved and restored independently of disabled generation.
They use the existing personal-owner gate and SecretBox encryption, with explicit save,
confirmed deletion, revision conflicts and account/reference isolation. A draft never
creates a job or calls Broker; availability changes cannot automatically generate it.
Migration 0038 preserves drafts and deletion revisions on downgrade. Unsaved edits remain
memory-only; same-filename image replacement is not a reference-version change.

Studio speech/video controls are intentionally disabled, with capability verification
explicitly unknown. The dormant service uses existing Broker ports and a durable,
encrypted operation journal; unresolved outcomes block retries, including new request IDs.
No owner binding, provider grants, budget or media-recognition settings are activated.
See [Studio gates, recovery semantics and CI coverage](docs/studio.md).

Studio hardening adds bounded response streams, WAV/MP4 container checks, owner-only
operation history after reload and GET-only reconciliation of retained video job IDs.
Lost video receipts and synchronous speech outcomes remain blocked: the reviewed Broker
contract does not provide safe correlation for them. Generation/reconciliation stay
hard-disabled; this is not permission or readiness to activate paid media routes.

# Summary integrity

Summary updates reject provider-truncated (`length` / `max_tokens`, normalized by
`LLMResult.truncated`) or over-6000-character results before changing stored memory,
coverage, model or timestamps. They do not silently keep a prefix. Reply preparation
uses the existing saved summary on this error; explicit refresh follows the existing
error/transaction rollback path. No immediate retry or background retry loop is added.
A later normal invocation can try the still-uncovered messages again. This does not
recover historically lost memory or guarantee semantic completeness of model output.
Synthetic CI regressions cover first creation, existing memory, exact-size success,
repeat refresh, a late incomplete result racing a successful update, and rollback of
a multi-batch refresh. The separate 400-row history disclosure and assembled-context
UI gaps are not changed here. No provider calls or production settings are required.


### Этапы ответа в тестовом чате персонажа

В «Проверить персонажа» переключатели действуют только для текущего тестового
чата, не сохраняются в анкете и не затрагивают Inbox, Telegram или другие каналы.
Пробный ответ не отправляется клиенту. Настройки применяются к следующему
запросу или повтору; «Очистить» возвращает исходные настройки.

- **Пауза перед ответом** и **ожидание по расписанию** по умолчанию не
  рассчитываются. Если их включить, «Этапы ответа» покажут оценку ожидания
  до начала ответа на одно новое сообщение по общим правилам планировщика.
  Ответ приходит без искусственного ожидания; это не время работы модели,
  не пауза между частями сообщения и не имитация очереди живого диалога.
- **Решения прошлых реплик** по умолчанию передаются в следующий запрос
  (этап воронки, потребности, следующий шаг, тестовая передача оператору).
  Если их отключить, эти данные сбрасываются только в тестовом чате.
  Отказ от общения не сбрасывается переключателем.
- Кнопка **«Основной промпт без контекста решений»** отключает эти три
  параметра. Сохранённый промпт, история, распорядок как факт о персонаже,
  выбор модели, формат и обязательный контролёр остаются. Отдельные
  планировщики, генерация паспорта и действия в каналах здесь не запускаются.

`POST /api/personas/{id}/preview` принимает необязательный объект `stages`
с булевыми ключами `debounce`, `schedule`, `decision_context`.
Без него сохраняется прежний режим; в ответе `stages` содержит фактические
параметры теста и оценку ожидания в секундах.

#### Предпросмотр фото и видеокружка

В «Проверить персонажа» кнопка «Показать фото из галереи» открывает первое
фото из галереи текущего персонажа по существующему маршруту с авторизацией.
Если галерея пуста, чат сообщит об этом. «Показать тестовый кружок» открывает
в том же чате встроенный круглый плеер с коротким MP4 из приложения.
Кнопка «Очистить» убирает образец. Эти действия не входят в историю для модели,
не создают сообщений или медиа в живых диалогах и ничего не отправляют в Telegram.

### Видеокружочки из чата

Сгенерированный по просьбе в чате кружочек может уйти только в исходный
диалог на том же Telegram-канале и разрешённому получателю. Пауза ИИ,
отказ собеседника, отзыв разрешения или архивирование канала отменяют
отправку. Причина отображается в записи решения о медиа. Нужна звуковая
дорожка в исходном видео. Если дорожки или файла нет, текстовая заглушка
не отправляется. Ручной инструмент владельца работает по прежним правилам.

Для проверки в CI используйте синтетическое видео со звуком и два канала
с одним тестовым получателем. Проверьте доставку только на исходный канал,
затем повторите сценарии с немым видео, пропавшим файлом и паузой или
отзывом разрешения во время резервирования отправки. Для старого задания
без привязки или актуальной отметки проверки звука чатовый кружочек
отменяется при отсутствии квитанции. Подтверждённая ранее отправка
восстанавливается при совпадении отпечатка маршрута. Неопределённый
исход остаётся неопределённым. Повторной доставки или генерации нет.
Ручной маршрут использует прежние правила.

Для чатового кружочка проверяется сигнал в готовой звуковой дорожке.
Короткие паузы допустимы. Проверка отсекает цифровую тишину и не определяет,
есть ли в видео речь. В CI это проверяется на MP4 с немым AAC и на тихом
синтетическом сигнале после паузы.

Для фото и кружка из чата журнал показывает время выбора референсов в UTC
и идентификаторы выбранных фото из галереи персонажа. Worker использует именно
эти фото в сохранённом порядке. Для фото требуется от одного до трёх
разных референсов, для кружка ровно один. Если выбранное фото удалено,
повреждено, превышает 4 МБ или не соответствует своему формату, генерация
отменяется до запроса к провайдеру.
Старые задания без сохранённых референсов также отменяются. Ручной инструмент
владельца сохраняет прежние правила выбора референса.

При постановке фото или кружка в очередь журнал сохраняет момент выбора
в UTC и условное освещение кадра по часовому поясу персонажа. Worker
использует тот же снимок, даже если время или расписание успели измениться.
Освещение описывает синтетический кадр. Расписание не передаётся генератору
как свидетельство о месте или занятии. Старый план без снимка можно
восстановить из записи в том же диалоге, если совпадают вид медиа, сцена и
порядок референсов, запись находится в состоянии ожидания генерации и время
записано в UTC. Возраст задания и расхождение часов не влияют на восстановление.
Для восстановленного кадра используется нейтральное освещение.
Повреждённые снимки и планы без подходящей записи отменяются до обращения
к провайдеру.

Нейтральный кадр возможен по первой простой просьбе «пришли фото» или
«пришли кружочек», если модель выбрала медиа того же типа и её ответ состоит
из короткого «хорошо» или «ок». При более ранних репликах, включая сообщения
в той же пачке, саммари, паспорте или усечённой истории сохраняется сцена модели.
Если черновик ответа называет конкретное место или занятие, исходная сцена сохраняется.
Явно запрошенные сцены и действующие ограничения отправки сохраняются.

В одном диалоге одновременно допускается только одна генерация фото или
кружка. Она занимает место и в очереди, и во время выполнения, в том числе
для владельца без паузы между медиа. Это не меняет решение модели, проверку
доступности провайдера или правила восстановления неопределённой отправки.

### Очередь тестового чата персонажа

После штатной выкладки с миграцией 0039 карточка персонажа предлагает
«Обычный, с настройками озвучки» и «Через очередь, только текст».
Обычный режим выбран при открытии. Режим очереди выбирается явно. Если
флаг выключился после выбора, интерфейс просит выбрать обычный режим и не
перенаправляет запрос. Статус ранее принятой операции доступен в обоих режимах.

Production Compose включает очередь по умолчанию для API и worker.
Для отката без изменения приложения установить в конфигурации Compose
`SIM_PREVIEW_ASYNC_ENABLED=false` и применить её к обоим сервисам.
При выключении ещё не начатые запросы отменяются и не запускаются повторно.
Результаты, записанные до выключения, доступны до истечения срока хранения.
Вне этого Compose значение по умолчанию остаётся выключенным.
Включение проходит штатные GitHub CI и миграции.

Проверка после выкладки без вызова моделей: открыть карточку, проверить
оба варианта, выбрать текстовый и увидеть пояснение. В браузерном журнале
сети убедиться, что переключение не вызывает POST. Если сохранился ID
ранее принятого запроса, перезагрузка должна обращаться к его GET и с
выключенной очередью. Маршруты отправки и озвучку проверяет CI на подменах.

POST с идентификатором вида `<unix-ms>-<uuid-v4>` возвращает 202 после коммита
заявки и задания в одной транзакции. GET возвращает состояние и готовый ответ.
Браузер хранит только ID, отдельно для каждого аккаунта и каждой персоны.
После обновления страницы или потери подтверждения браузер проверяет прежний
ID через GET. Другой пользователь или персона не получат статус и историю.

Одновременно принимается до 16 незавершённых запросов, по одному на
пользователя. Переполнение возвращает 429 с областью лимита. Повтор принятого
ID с тем же телом возвращает существующую операцию даже при переполнении или
отключённом флаге. Другое тело с тем же ID отклоняется. Если сервер так и не
получил запрос, после двухминутного окна приёма GET подтверждает истечение ID.
До этого отсутствие записи не считается доказательством, что POST не дошёл.

Рабочая очередь сохраняет прежний порядок. Preview выбирается отдельно при
наличии свободного слота, по одной операции на весь worker-кластер. После
потери worker выполнявшийся запрос переходит в состояние «исход неизвестен».
Очередь не запускает его повторно. При изменении конфигурации до старта
запрос останавливается до вызова модели. Отключение флага прекращает приём,
убирает ещё не начатые задачи, а готовые ответы по-прежнему доступны через GET.

Асинхронная ветка пока работает только с текстовым ответом. TTS не вызывается:
у синтеза через Broker нет подтверждённого способа разрешить неопределённый
исход без повтора. Интерфейс сообщает об этом до запроса. Синхронный
предпросмотр сохраняет прежнюю возможность озвучивания. Распознавание
входящего вложения остаётся отдельным вызовом.

Ответ и введённый текст доступны до часа. Отдельная задача worker очищает
данные и квитанции Broker внутри SIM независимо от чистки трасс. Служебная
запись ID хранится до истечения окна дедупликации. Содержимое запросов и
ответов очереди не попадает в общий журнал. Срок хранения данных в Broker
и резервных копиях определяется отдельно.

В CI с подменой провайдера проверяются Save → Send, обновление
страницы после потери POST-ответа, выключение флага, смену персоны и аккаунта,
конкурентную постановку, потерю worker и работу обычной очереди под нагрузкой.

### Асинхронная подпись публикации

Редактор сначала сохраняет черновик, затем ставит генерацию через
`POST /api/content/{post_id}/caption-operations/{key}`. Ответ 202 подтверждает
постановку. `GET` того же маршрута возвращает статус и предложение. Браузер
запоминает только ID в рамках аккаунта и публикации. После обновления он
возобновляет чтение, а не отправляет новый запрос. Для аккаунта, публикации
и ревизии сохранённого черновика действует одна операция, включая готовые
и неопределённые результаты, пока хранится запись об операции. Поздний POST
возвращает исходный ID. Если его ответ потерялся, GET по переданному ID находит
ту же операцию, если алиас записан. На операцию хранится до восьми алиасов.
При заполнении лимита и при выключенной очереди повторный POST возвращает
канонический ID без записи нового алиаса. Браузер сохраняет канонический ID
сразу после ответа. Если ответ без записанного алиаса потерян, браузер
не отправляет новую генерацию автоматически. Устаревшая кнопка синхронного
клиента получает 428 с просьбой обновить страницу.

Генерация использует существующую ограниченную очередь приватных запросов и
квитанции текстового Broker. У подписи свой контекст публикации. Чужой аккаунт,
другая публикация и предпросмотр персонажа не получают результат. Текст запроса
и ответа не попадает в общий трассировочный журнал. Результат доступен до часа
и очищается вместе с квитанциями.

Изменение сохранённой публикации или настроек промпта делает предложение
неактуальным. Worker сверяет ревизию до генерации, API сверяет её перед выдачей.
Предложенная подпись сохраняется отдельно. Она попадает в поле редактора только
после «Вставить подпись», а в публикацию после обычного сохранения. Потеря worker
не запускает генерацию повторно.

Миграция 0040 добавляет привязку к публикации. Для приёма нужен действующий
`SIM_PREVIEW_ASYNC_ENABLED`. При отключении флага новые запросы не поступают,
а готовый доступный результат можно читать по прежнему ID. Тесты API и интерфейса
работают с подменами. Реальную генерацию в проверку CI не включаем.
