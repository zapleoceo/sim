# Sim

ИИ-персонажи (виртуальные представители): Instagram — привлечение, WhatsApp и Telegram — личное
общение, воронка до регистрации и первой оплаты у партнёра. Документированный URL: https://sim.zapleo.com.

- Архитектура и решения: [docs/architecture.md](docs/architecture.md)
- HTTP API: [docs/api.md](docs/api.md)
- Переписка, активность и подключение каналов: [docs/admin-activity.md](docs/admin-activity.md)
- Персонажи и стратегия: [docs/personas.md](docs/personas.md)
- Запуск и эксплуатация: [docs/operations.md](docs/operations.md)
- CI, тесты и деплой: [docs/ci.md](docs/ci.md)
- Админка (стек, структура, тесты, сборка): [docs/admin-frontend.md](docs/admin-frontend.md)
- Старые отчёты, ревью и задачи (история, не описание текущего кода): [docs/history/](docs/history/)

## Состояние реализации

В коде есть персонажи, каналы, единый inbox, воронка, аналитика и симуляции. Для Instagram
подключён ограниченный путь публикации одного локального JPEG, по умолчанию выключенный и
требующий явного списка разрешённых post ID. Долговечный intent предотвращает повторный вызов
upload для того же поста, receipt позволяет восстановить локальный статус при совпадении
payload/account. Это не полная готовность публикаций из UI.
См. [ограничения пилота](docs/instagram-content-publishing.md) и
[intent, receipt и восстановление](docs/publication-intents.md).
Входящие голосовые обрабатываются отдельным путём транскрипции; голосовые ответы описаны в
[docs/voice.md](docs/voice.md).

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
shadcn/ui, TanStack Query/Router/Table: [docs/admin-frontend.md](docs/admin-frontend.md). Тесты
(pytest, Vitest), сборка, линт и типы запускаются только в CI (см. [AGENTS.md](AGENTS.md) и
[docs/ci.md](docs/ci.md)).

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

## Ограничения доставки

Generation/CAS fencing очереди и долговечный intent/receipt исходящих сообщений не обещают
exactly-once внешних действий; смешанный rollout со старыми worker небезопасен. См.
[контракт доставки](docs/outbound-delivery.md) и
[fencing состояния очереди](docs/api.md#fencing-состояния-очереди).
