# Sim

Исходники main включают PR #1–#13; PR #13 слит commit `7ef50b1f2481f1ed9dde03c264075ea82ba86529`.
Единая переписка клиента и журнал активности описаны в [задаче](docs/ai-ui/tasks/unified-client-admin/task-context.md).
Последняя read-only проверка production 2026-11-07 показала ревизию `b27a89b` и миграцию
`0005`; выкладка PR #13 не подтверждена.

ИИ-персонажи (виртуальные представители): Instagram — привлечение, WhatsApp и Telegram — личное
общение, воронка до регистрации и первой оплаты у партнёра. Документированный URL: https://sim.zapleo.com (текущий релиз и доступность не проверены).

- Архитектура и решения: [docs/architecture.md](docs/architecture.md)
- HTTP API: [docs/api.md](docs/api.md)
- Переписка, активность и подключение каналов: [docs/admin-activity.md](docs/admin-activity.md)
- Персонажи и стратегия: [docs/personas.md](docs/personas.md)
- Игра Sim1 (словарь команд, согласие, настройки): [docs/game.md](docs/game.md)
- Цели и состояние возможностей: [docs/project-status.md](docs/project-status.md)
- Запуск, проверки и эксплуатация: [docs/operations.md](docs/operations.md)
- Админка (стек, структура, тесты, сборка): [docs/admin-frontend.md](docs/admin-frontend.md)
- Статическое ревью и приоритеты: [docs/review-2026-11-07.md](docs/review-2026-11-07.md)

## Состояние реализации

В коде есть персонажи, каналы, единый inbox, воронка, аналитика и симуляции. Это не
подтверждение работы текущего production. Для Instagram подключён ограниченный путь публикации одного локального JPEG,
по умолчанию выключенный и требующий явного списка разрешённых post ID. Долговечный intent
предотвращает повторный вызов upload для того же поста, receipt позволяет восстановить
локальный статус при совпадении payload/account. Это не полная готовность публикаций из UI.
См. [ограничения пилота](docs/instagram-content-publishing.md) и
[intent, receipt и восстановление](docs/publication-intents.md).
Исходящие индивидуальные голоса и видео также не следует считать готовыми; входящие голосовые
обрабатываются отдельным путём транскрипции. Подробнее — в матрице возможностей выше.

## Локально

```bash
cd backend && uv sync && cp .env.example .env   # заполнить SIM_SECRET_KEY, SIM_BROKER_PROJECT_KEY
uv run alembic upgrade head && uv run python -m sim.seed
uv run python scripts/demo_data.py                 # по желанию: демо-данные для UI, только SQLite;
                                                  # печатает одноразовую ссылку входа (SIM_PUBLIC_URL, по умолчанию :4200)
uv run uvicorn sim.api.main:create_app --factory --port 8000
uv run python -m sim.worker                       # отдельный терминал
uv run pytest -q && uv run ruff check .
cd ../frontend && npm ci && npm run dev         # http://localhost:4200 (Vite), прокси /api и /hooks на :8000
```

Используйте изолированную локальную БД и тестовые настройки каналов. API и worker с действующими
учётными данными могут обращаться к внешним сервисам. Админка — React + Vite + TypeScript,
shadcn/ui, TanStack Query/Router/Table: [docs/admin-frontend.md](docs/admin-frontend.md). Сборка, линт,
типы и тесты (Vitest) запускаются только в CI (см. AGENTS.md).

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

## Проверенная база

Для PR #13 облачная проверка точного исходника: 573 backend-теста, Ruff, frontend build,
lint и TypeScript app/spec прошли. Полный mypy сохраняет 18 прежних ошибок в шести файлах;
пять затронутых модулей прошли выборочную проверку. Локально на Windows прошли 91/91
ChromeHeadless-тестов и адаптивное ревью; в облаке Chromium не запустился из-за запрета socket.
Эти проверки не подтверждают PostgreSQL и работу реальных каналов.

Generation/CAS fencing очереди и долговечный intent/receipt исходящих сообщений слиты.
Они не обещают exactly-once внешних действий; смешанный rollout со старыми worker
небезопасен. См. [контракт доставки](docs/outbound-delivery.md) и
[ограничения проекта](docs/project-status.md).
