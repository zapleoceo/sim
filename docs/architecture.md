# Sim — архитектура MVP

Платформа ИИ-персонажей: привлечение в Instagram, личное общение в WhatsApp и Telegram,
сопровождение клиента по воронке до регистрации и первой оплаты у партнёра.

Прод: `https://sim.zapleo.com` (Cloudflare → nginx :80 на 195.201.31.49 → docker compose).
LLM: только через AIbroker (`https://aib.zapleo.com`, проект `sim`, id 21, лимиты $0 — бесплатные
провайдеры + подписочные `claude-sub/*`, `codex-sub/*`).

## Принципы

1. **Гексагональная архитектура.** `domain` ничего не импортирует; `services` зависят только от
   `ports` (Protocol); `adapters` реализуют порты; `api`/`worker` собирают граф зависимостей
   (`sim/wiring.py`). Новый канал — это новый адаптер + строка в реестре коннекторов, а не правки
   в десяти местах (идея и большая часть кода — из stepan2, `app/connectors/`).
2. **LLM — только Broker.** Ключей провайдеров в проекте нет. Модель задаётся на уровне персонажа
   (`persona.model`: пусто = цепочка Broker `chat:sales`, или точный pin `claude-sub/claude-sonnet-5-5`).
3. **Секреты каналов шифруются** (Fernet/MultiFernet, ключ из env, ротация через список ключей).
   Вход админов — только Telegram Login Widget (паролей нет). Сессии — httpOnly + Secure + SameSite=Lax cookie, CSRF-токен
   на мутирующих запросах.
4. **Персонаж — виртуальный представитель.** Это зафиксировано в персоне и в системном
   промпте: на прямой вопрос «ты бот/человек?» персонаж честно отвечает, что он виртуальный ИИ-
   ассистент. Ложь об этом запрещена промптом и проверяется тестом на сборку промпта.
5. **Оплаты и регистрации подтверждает только партнёр** (webhook с HMAC или импорт CSV),
   не ИИ и не оператор вручную по словам клиента.
6. **Без Redis.** Очередь задач — таблица `job` в Postgres (`FOR UPDATE SKIP LOCKED`). На сервере
   ~1 ГБ свободной RAM, лишний сервис не нужен.

## Модули (backend, Python 3.12, FastAPI, SQLAlchemy 2 async, Alembic)

```
backend/sim/
  config.py            pydantic-settings, префикс SIM_
  domain/              enums, воронка (переходы этапов), чистые правила; без IO
  ports/               Protocol-интерфейсы: ChannelPort, LLMPort, VoicePort, Clock
  adapters/
    crypto.py          Fernet (из stepan2)
    llm/broker.py      клиент Broker: POST /v1/jobs → poll GET /v1/jobs/{id} (из stepan2, упрощён)
    channels/          telegram.py (Bot API), whatsapp.py (Evolution API), instagram.py (instagrapi)
    db/                ORM-модели, репозитории
  connectors/          ConnectorSpec + REGISTRY (kind → как собрать порт, capabilities)
  services/
    auth.py            админы, сессии, Telegram-вход, роли (superadmin / owner / operator)
    ingest.py          входящее сообщение → клиент/диалог/сообщение, дедуп по external_id
    conversation.py    сборка промпта (персона + стратегия + этап + история) → Broker → JSON-решение
    outbound.py        отправка через канал, «человеческая» задержка, ручной режим оператора
    funnel.py          смена этапов, отказ/пауза/нет ответа/возврат, журнал событий
    followup.py        повторный контакт при отсутствии ответа (по сценарию)
    partner.py         подтверждения регистраций и оплат от партнёра
    analytics.py       сводка: контакты, этапы, конверсии, расходы, выплаты
    content.py         контент Instagram: черновик → согласование → план → публикация
  api/                 FastAPI-роутеры (/api/...), webhooks (/hooks/...)
  worker/              цикл: опрос Instagram, выполнение задач из таблицы job
  wiring.py            сборка зависимостей
```

## Модель данных (главное)

| Таблица | Назначение |
|---|---|
| `admin_user` | `telegram_id` (уникальный, пусто до первого входа по приглашению), `tg_username`, имя, фото, роль (`superadmin`/`owner`/`operator`), активность; `used_login_hash` (одноразовые hash виджета), `login_link` (break-glass) |
| `persona` | имя, возраст, характер, биография, стиль, язык, модель, голос (ElevenLabs voice_id), аватар, `is_virtual_disclosed` |
| `strategy` | сценарий общения: цель, этапы, правила, оффер партнёра, ссылка регистрации |
| `channel` | kind (`instagram`/`whatsapp`/`telegram`), persona_id, зашифрованные учётные данные, статус сессии |
| `client` | человек; persona_id; этап воронки; статус (`active`/`paused`/`refused`/`no_response`); следующий шаг; заметки |
| `client_identity` | (channel_kind, external_id) → client — один человек в нескольких каналах |
| `dialog` | тред канала: channel_id + external_thread_id → client; `ai_paused`, `operator_id` |
| `message` | направление, автор (`client`/`ai`/`operator`), текст, медиа, external_id, токены/модель/стоимость |
| `funnel_event` | смена этапа/статуса: откуда, куда, кто, почему |
| `partner_event` | регистрация/оплата от партнёра: внешний id, сумма, валюта, выплата нам |
| `content_post` | контент IG: тип, подпись, медиа, статус (`draft`/`approved`/`scheduled`/`published`), время |
| `job` | очередь: kind, payload, run_at, attempts, locked_until |
| `llm_call` | аудит каждого вызова Broker (модель, токены, cost, latency, ошибка) |

## Воронка

Этапы (по порядку): `first_contact` → `engaged` → `needs_identified` → `interested` →
`handed_to_partner` → `registered` → `paid`.

Статусы поверх этапа: `active`, `paused` (оператор/клиент попросил паузу), `no_response`
(лестница фоллоу-апов исчерпана), `refused` (отказ). Возврат к диалогу: новое входящее
сообщение переводит `no_response`/`paused` обратно в `active`, этап не теряется.

ИИ может продвигать этап только до `handed_to_partner` включительно и только вперёд.
`registered` и `paid` ставит исключительно `partner.py` по данным партнёра.

## Поток сообщения

```
канал ──webhook/poll──▶ ingest ──▶ message(in) + job(reply, run_at=now+debounce)
                                   └▶ job(media) для вложения (webhook сети не трогает)
worker ──▶ job(media): channel port.download_media(ref) → файл; голос → Broker transcribe → text
worker ──▶ job(reply): ai_paused? → стоп; открытый media-job диалога → ждать (повтор через 3 с)
                     один reply на диалог (advisory lock); промпт → Broker → {reply, stage, ...}
                     пришло новое сообщение клиента за время вызова → ответ отбрасывается,
                     следующий job отвечает с полным контекстом
                     funnel.advance(...) ; outbound.queue_ai(reply, delay) → job(send)
оператор в админке ──▶ outbound.send(manual): ИИ на паузе, неотправленные ответы ИИ отменяются
```

Правки сообщений Telegram (`edited_message`) сохраняются с пометкой «[изменено]», но нового
ответа ИИ не вызывают. Правка approved/scheduled поста оператором (не owner) возвращает его в `draft`.

Ответ модели — JSON (`response_format: json_object`). Если JSON не разобрался — один повтор, затем
диалог помечается для оператора, клиенту ничего не уходит.

## Prompt caching

Порядок сообщений, отправляемых в Broker: `system` = неизменный текст персоны + стратегии
(одинаков байт в байт для всех клиентов персонажа: без дат, имён, id), затем история диалога,
затем `system`-блок с динамикой (этап, заметки, текущее время). Брокер сам ставит брейкпоинты
для Anthropic; эффект проверяется по `cache_read_input_tokens` в `llm_call`.

## Каналы

| Канал | Транспорт | Как подключается | Приём | Риски |
|---|---|---|---|---|
| Telegram (бот) | Bot API (официально) | токен бота от @BotFather | webhook `/hooks/telegram/{channel_id}` + `secret_token` | нет |
| Telegram (аккаунт, `telegram_user`) | Telethon (MTProto, личный аккаунт, userbot) | `api_id`/`api_hash` с my.telegram.org → API development tools, телефон, код, пароль 2FA | опрос раз в ~20 с в worker (`SIM_TG_USER_POLL_INTERVAL_S`) | ограничение/бан аккаунта при подозрительной активности; FloodWait |
| WhatsApp | Evolution API v2 (Baileys, self-hosted) | QR-код в админке | webhook Evolution → `/hooks/evolution/{channel_id}` (внутренняя сеть, apikey) | неофициальный клиент, риск бана номера |
| Instagram | instagrapi (private API) | логин/пароль (+2FA/challenge код) в админке | опрос раз в ~2 мин в worker | риск checkpoint/бана; прокси с geo как у логина |

Telegram-аккаунт (`telegram_user`): StringSession (api_id, api_hash, string_session, прокси) хранится
зашифрованно в `channel.session_enc`; код входа и пароль 2FA нигде не сохраняются. Незавершённый
вход лежит в сессии под `pending` (с `phone_code_hash`), поэтому переживает рестарт. Клиент
подключается без подписки на обновления (`receive_updates=False`) и ничего не помечает
прочитанным; читаются только личные диалоги с людьми (без групп, каналов, ботов и 777000).
Курсор по сообщениям (id на собеседника) живёт в памяти worker; после рестарта подхватываются
только непрочитанные и пришедшие за последние 24 ч (дубли отсекает `external_id`).
Отправка: чанки по 4096, минимум 3 с между отправками с аккаунта (`SIM_TG_USER_MIN_SEND_INTERVAL_S`),
FloodWait на отправке даёт `flood_wait:<секунды>` и повтор job по backoff. Отвязка делает `log_out`,
чтобы не оставлять «призрачное» устройство в сессиях аккаунта. Новое значение `ChannelKind` миграции
не требует: enum хранится как VARCHAR(32) без CHECK.

Голос: входящие голосовые → Broker `/v1/transcribe/jobs`. Исходящий голос (ElevenLabs) и видео
(Vivix) — `VoicePort`/`VideoPort` с заглушкой; подключаются после проверки API и цены.

## Безопасность

- Webhook Telegram: проверка `X-Telegram-Bot-Api-Secret-Token` (секрет на канал).
- Webhook Evolution: доступен только из docker-сети, nginx не проксирует `/hooks/evolution`.
- Webhook партнёра: HMAC-SHA256 тела, секрет в env, защита от replay (timestamp ± 5 мин).
- Логин только через Telegram Login Widget: HMAC-SHA256 (секрет = sha256 токена бота), сравнение
  за постоянное время, `auth_date` не старше 1 часа и не из будущего (допуск 60 с), `hash`
  одноразовый (в БД, чистится по сроку); доступ только у активных админов из `admin_user`
  (приглашение по id или @username, `SIM_SUPERADMIN_TELEGRAM_IDS` — всегда superadmin).
  Break-glass: `python -m sim.cli login-link <id>` — одноразовая ссылка на 10 минут (sha256 в БД).
  Управление админами — только superadmin; последнего superadmin и себя убрать нельзя.
- Логин: rate limit по паре (telegram id, IP) и по IP (отдельного лимита на id нет — иначе чужой может
  заблокировать владельца). IP клиента берётся из `X-Real-IP`/`X-Forwarded-For` только если TCP-пир
  входит в `SIM_TRUSTED_PROXIES` (по умолчанию `127.0.0.1,::1,172.16.0.0/12`: nginx + docker-шлюз).
- В `SIM_ENV=prod` `/api/docs` и `/api/openapi.json` отключены.
- Все запросы к БД — через ORM/параметры; входящий текст клиента идёт в промпт только как
  user-сообщение, не в system (защита от prompt injection в персону).
- В логах нет секретов и полных текстов учётных данных.

## Решения «брать готовое / писать своё»

| Кандидат | Вердикт | Почему |
|---|---|---|
| stepan2 коннекторы (instagrapi, Evolution, Broker-клиент, crypto, ConnectorSpec) | брать | рабочий прод-код того же владельца, проверен на банах и таймаутах |
| Evolution API v2.3.7 | брать | уже используется в stepan2; Apache-2.0; self-hosted |
| instagrapi | брать | единственный живой вариант логина в IG без Meta App Review |
| Telegram Bot API напрямую через httpx | брать | 3 метода, отдельная библиотека не нужна |
| Telethon (userbot для `telegram_user`) | брать | MIT, живой (1.45.0), StringSession, `receive_updates=False`; прокси через python-socks |
| Pyrogram | нет | не поддерживается (заброшен); форки без гарантий |
| Redis/arq | нет | лишний сервис на сервере с 1 ГБ свободной RAM; хватает Postgres SKIP LOCKED |
| stepan2 целиком (мультифилиальность, CRM, MBS, реклама) | нет | другой домен; тянет 90% ненужного |
| Angular 19 (standalone, signals) для админки | брать | основной фронтенд-стек команды |
| Flask + itstep-* пакеты (правило `python.md`) | нет | Sim — проект zapleo, не itstep: внутренний PyPI и сервисы itstep отсюда недоступны; берём стек stepan2 (FastAPI, uv), чтобы переносить его код без переписывания |
