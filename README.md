# Sim

ИИ-персонажи (виртуальные представители): Instagram — привлечение, WhatsApp и Telegram — личное
общение, воронка до регистрации и первой оплаты у партнёра. Прод: https://sim.zapleo.com

- Архитектура и решения: [docs/architecture.md](docs/architecture.md)
- HTTP API: [docs/api.md](docs/api.md)
- Персонажи и стратегия: [docs/personas.md](docs/personas.md)
- Цели и состояние возможностей: [docs/project-status.md](docs/project-status.md)
- Запуск, проверки и эксплуатация: [docs/operations.md](docs/operations.md)
- Статическое ревью и приоритеты: [docs/review-2026-11-07.md](docs/review-2026-11-07.md)

## Состояние реализации

В коде есть персонажи, каналы, единый inbox, воронка, аналитика и симуляции. Это не
подтверждение работы текущего production. Публикация Instagram из админки пока не подключена:
стандартный контейнер не задаёт `ContentPublisher`, и запланированный пост получает `failed`.
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
cd ../frontend && npm ci && npm start             # http://localhost:4200, прокси на :8000
```

Используйте изолированную локальную БД и тестовые настройки каналов. API и worker с действующими
учётными данными могут обращаться к внешним сервисам. Сборка frontend — `npm run build`,
unit tests — `npm test -- --watch=false` (требуется доступный браузер для Karma).

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
