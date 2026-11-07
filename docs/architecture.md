# Sim — архитектура MVP

Кандидат: main `a7736bcb3d493f25be340f064b92f46e9c2bb14b` + [PR #9](https://github.com/zapleoceo/sim/pull/9).
Слияние PR #9 и deployment не подтверждены; это описание подготовленного изменения.

База кода: `a7736bcb3d493f25be340f064b92f46e9c2bb14b`. Описание кода и репозиторной конфигурации,
не подтверждение текущего production. Контекст брифа: [состояние проекта](project-status.md).

Платформа ИИ-персонажей: привлечение в Instagram, личное общение в WhatsApp и Telegram,
сопровождение клиента по воронке до регистрации и первой оплаты у партнёра.

Документированная схема: `https://sim.zapleo.com` → Cloudflare → nginx :80 → docker compose.
Фактический origin TLS и версия production не проверены.
Репозиторная конфигурация LLM: только через AIbroker (`https://aib.zapleo.com`, проект `sim`, id 21, лимиты $0 — бесплатные
провайдеры + подписочные `claude-sub/*`, `codex-sub/*`).

## Принципы

1. **Слои и порты внешних систем.** `domain` содержит правила без IO; `ports` (Protocol)
   описывают каналы и LLM, `adapters` их реализуют; `api`/`worker` собирают зависимости
   (`sim/wiring.py`). В текущем коде `services` также напрямую используют SQLAlchemy,
   ORM-репозитории и инфраструктурные компоненты: строгой гексагональной изоляции пока нет.
   Новый канал добавляется через адаптер и реестр коннекторов (часть решений взята из stepan2).
2. **LLM — только Broker.** Ключей провайдеров в проекте нет. Модель задаётся на уровне персонажа
   (`persona.model`: пусто = цепочка Broker `chat:sales`, или точный pin `claude-sub/claude-sonnet-5-5`).
3. **Секреты каналов шифруются** (Fernet/MultiFernet, ключ из env, ротация через список ключей).
   Вход админов — только Telegram Login Widget (паролей нет). Сессии — httpOnly + Secure + SameSite=Lax cookie, CSRF-токен
   на мутирующих запросах.
4. **Персонаж живёт в роли, но не лжёт о своей природе.** Он говорит от первого лица, как
   женщина со своей биографией и живой речью; без вопроса не упоминает, что он ИИ, без
   дисклеймеров и «как ИИ я…». Нижняя планка честности (решение по продукту, не обсуждается):
   на прямой искренний вопрос «ты человек/бот/ИИ?» персонаж не утверждает, что он человек, а
   коротко и в образе отвечает, что он виртуальная помощница проекта, и возвращается к разговору
   (оператора предлагает только если попросили). Это правило есть и в системном промпте, и в
   контролере (см. «Контролер»); проверяется тестами на сборку промпта и контролера.
5. **Оплаты и регистрации подтверждает только партнёр** (webhook с HMAC или импорт CSV),
   не ИИ и не оператор вручную по словам клиента.
6. **Без Redis.** Очередь задач — таблица `job` в Postgres (`FOR UPDATE SKIP LOCKED`). Исходное обоснование в репозитории —
   ограниченная RAM сервера; текущий объём свободной памяти не измерялся.

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
| `persona` | имя, возраст, пол, аватар, язык, модель, голос (ElevenLabs voice_id), `strategy_id`, `is_active`, `disclosure_text`; **`description`** — один свободный текст (кто она, характер, биография, как пишет) и **`prohibitions`** — «чего нельзя», по правилу на строку |
| `strategy` | сценарий общения: цель (короткая формула), **`prompt`** (свободный текст: ориентиры разговора, не скрипт), подсказки по этапам, мягкие правила, оффер партнёра, ссылка регистрации |
| `channel` | kind (`instagram`/`whatsapp`/`telegram`), persona_id, зашифрованные учётные данные, статус сессии |
| `client` | человек; persona_id; этап воронки; статус (`active`/`paused`/`refused`/`no_response`); следующий шаг; заметки |
| `client_identity` | (channel_id, external_id) → client — один человек в нескольких каналах **одной персоны** (см. «Лид принадлежит персоне») |
| `dialog` | тред канала: channel_id + external_thread_id → client; `ai_paused`, `operator_id` |
| `message` | направление, автор (`client`/`ai`/`operator`), текст, медиа, external_id, токены/модель/стоимость; для сообщений ИИ — `guard_verdict` (`ok`/`rewritten`/`blocked`), `guard_violations` (JSON), `guard_model`, `original_text` (черновик генератора, если контролер его исправил) |
| `funnel_event` | смена этапа/статуса: откуда, куда, кто, почему |
| `partner_event` | регистрация/оплата от партнёра: внешний id, сумма, валюта, выплата нам |
| `content_post` | контент IG: тип, подпись, медиа, статус (`draft`/`approved`/`scheduled`/`published`/`failed`), время |
| `content_publish_intent` | постоянный intent по post ID; fingerprint, время попытки и внешний receipt; хранится независимо от удаления поста |
| `sim_branch`, `sim_message`, `sim_admin_pref` | симуляции админов в боте входа: ветка (админ, персонаж, этап/статус/потребности/следующий шаг/заметки, `closed_at`, `last_active_at`), её сообщения (`role` `client`/`ai`, вердикт контролера, нарушения, модель) и настройка `/debug`. Отдельно от `client`/`dialog`/`message` |
| `job` | очередь: kind (`reply`, `send`, `followup`, `media`, `content_publish`, `sim_reply`), payload, run_at, attempts, locked_until |
| `llm_call` | аудит каждого вызова Broker (модель, токены, cost, latency, ошибка); вызовы контролера — `workflow = "guard"`; симуляции — `sim` и `sim_guard` (в сводку расходов аналитики не входят) |

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
                     контролер (guard) проверяет черновик → ok / rewrite / block (см. ниже)
                     funnel.advance(...) ; outbound.queue_ai(текст после контролера, delay) → job(send)
оператор в админке ──▶ outbound.send(manual): ИИ на паузе, неотправленные ответы ИИ отменяются
```

История реального диалога включает входящие и только `sent` исходящие сообщения.
Фильтрация происходит до лимита истории; `queued`, `failed`, `cancelled` и guard-blocked
черновики не воспринимаются как увиденные клиентом. Inbox сохраняет все записи.
В кандидате PR #9 аналитический `replied` также учитывает только `sent` исходящие без
`guard_verdict=blocked`, сохраняя все входящие. Период и порядок по-прежнему определяются
по `created_at`, отдельного `sent_at` нет; это приближение хронологии доставки. Счётчики
`guard` не изменены (см. [ревью](review-2026-11-07.md)).

Правки сообщений Telegram (`edited_message`) сохраняются с пометкой «[изменено]», но нового
ответа ИИ не вызывают. Правка approved/scheduled поста оператором (не owner) возвращает е…9000 tokens truncated…два gate, явный post ID; долговечный intent/receipt, без автоматического retry неизвестного исхода; полноценный upload/UI и live-проверка отсутствуют |
| WhatsApp | Реализовано в коде | Evolution API, linked-device QR; неофициальный транспорт с риском блокировки |
| Telegram | Реализовано в коде | Bot API и Telethon user account; реальные сессии не проверены |
| Воронка и партнёр | Реализовано в коде | Этапы, HMAC webhook, CSV; строки без ID и даты отклоняются, повтор ключа пропускается; прежние дубли не исправляются |
| Админка | Реализовано в коде | Angular Material, inbox, клиенты, аналитика, настройки; UX не проверялся в браузере |
| Контролер ответов | Реализовано с ограничением | Проверка, rewrite/block; известная одинаковая fallback-модель блокируется, но missing ID, aliases и self-rewrite остаются ограничениями |
| Симуляции | Реализовано в коде | Отдельные ветки в боте входа и read-only просмотр owner+ |
| История ИИ и очередь | Исправлено в коде | Только доставленные исходящие в истории; rolling pool до четырёх job, без distributed fencing |
| Финансовая аналитика | Исправлено в коде | Отдельные суммы по валютам, без FX; legacy scalar nullable. Кандидат PR #9 исключает из `replied` неотправленные/blocked исходящие; порядок остаётся по `created_at` |
| Production | Не проверено | URL и схема деплоя описаны; фактическая версия и состояние не подтверждены |

## Что ещё требует решения владельца
1. Подтвердить актуальные предложение RCIPM, цены и регистрационную ссылку. В docs/personas.md они отмечены как требующие уточнения.
2. Определить критерии готовности голосов, аватаров и публикаций Instagram.
3. Выбрать ожидаемые задержки ответа и минимальный объём одновременных диалогов для нагрузочной проверки.

Валютная политика и приём CSV уже определены в [API](api.md); повторное решение владельца
для них не требуется. Доставленные исходящие в `replied` исправлены в кандидате PR #9; слияние и deployment
не подтверждены. Следующие открытые направления: nginx login route, job fencing и
ограничения независимости guard. Остальные ограничения: [ревью](review-2026-11-07.md).

## Последняя локальная проверка
464 backend-теста и Ruff прошли; TypeScript app/spec и ESLint ранее прошли на неизменённом frontend. Spec только
скомпилированы, browser/Karma не запускались. Последняя Angular build не подтверждена:
сетевой доступ к npm registry не был разрешён. Проверки production и фактического релиза нет.
Для пилота Instagram см. [условия включения](instagram-content-publishing.md) и
[intent/receipt](publication-intents.md).

Источники: [описание проекта](https://github.com/zapleoceo/sim/blob/a7736bcb3d493f25be340f064b92f46e9c2bb14b/README.md), [персонажи и стратегия](https://github.com/zapleoceo/sim/blob/a7736bcb3d493f25be340f064b92f46e9c2bb14b/docs/personas.md), [API](https://github.com/zapleoceo/sim/blob/a7736bcb3d493f25be340f064b92f46e9c2bb14b/docs/api.md), [ContentService](https://github.com/zapleoceo/sim/blob/a7736bcb3d493f25be340f064b92f46e9c2bb14b/backend/sim/services/content.py), [сборка зависимостей](https://github.com/zapleoceo/sim/blob/a7736bcb3d493f25be340f064b92f46e9c2bb14b/backend/sim/wiring.py).
