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
Reuse assumes unchanged effective context: history-window expiry, a call incident's
24-hour expiry or media cooldown expiry may intentionally require a fresh generation
after prior requests settle. This does not guarantee zero additional generation cost
at these boundaries; uncertain requests still block resubmission. See the contract
document for this distinction and the real-handler incident-expiry regression.
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

