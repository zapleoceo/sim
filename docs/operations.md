# Запуск тесты и деплой SIM

## Безопасная локальная подготовка
Нужен полный checkout конкретного commit, Python 3.12+, uv, Node/npm. Версию Node следует согласовать с Angular 19 и lockfile; в package.json отдельный engines-контракт не задан. Не запускайте приложение из неполного набора файлов.

В полном изолированном checkout:
1. В backend выполнить uv sync.
2. Создать локальную .env из .env.example. Использовать только локальную БД и тестовые настройки; не копировать production-секреты.
3. Заполнить требуемую конфигурацию через безопасный механизм среды. Имена основных параметров: SIM_SECRET_KEY, SIM_BROKER_PROJECT_KEY.
4. Для локальной БД выполнить uv run alembic upgrade head и uv run python -m sim.seed.
5. Запустить API: uv run uvicorn sim.api.main:create_app --factory --port 8000.
6. Отдельно запустить uv run python -m sim.worker.
7. В frontend выполнить npm ci и npm start; localhost:4200 проксирует backend :8000.

API/worker могут обращаться к внешним каналам и Broker при действующих настройках. Для UI smoke использовать только изолированные данные и отключённые реальные каналы. scripts/demo_data.py создаёт данные и одноразовый вход, поэтому это изменяющая операция, не read-only диагностика.

## Команды проверки
- Backend: uv run pytest -q.
- Статический анализ: uv run ruff check .
- Frontend: npm run build.
- Frontend unit tests: npm test -- --watch=false; доступность браузера для Karma нужно проверить отдельно.
- Не считать build заменой unit tests или браузерного UX-теста.
- Отдельный PostgreSQL suite должен проверять миграции, advisory locks, SKIP LOCKED и конкурентные webhook/jobs.
- Прогнать сценарии CSV duplicate, одинаковой fallback-модели guard, delivery failure, отменённой истории, смешанных валют и зависшего LLM job.

Эти команды проверены по конфигурационным файлам. При ревью 7 ноября 2026 года полный suite не запускался; результатов pass/fail приложения нет.

## Документированный production-путь
URL: https://sim.zapleo.com. В репозитории: Cloudflare → nginx → Docker Compose. API публикуется на loopback:8010. Compose поднимает postgres, evolution, migrate, api и worker.

Штатный infra/deploy.sh:
1. Запускает pytest и ruff.
2. Проверяет npm зависимости и собирает frontend.
3. Проверяет наличие незакоммиченных изменений в указанных каталогах.
4. Передаёт архив закоммиченного HEAD и собранную админку по SSH.
5. Пересобирает и перезапускает Docker Compose.
6. Выполняет health smoke.

Alias по умолчанию: hetzner-root; override SIM_DEPLOY_HOST. Каталог: /var/www/sim. Скрипт сохраняет серверную infra/.env и broker-project.json, но удаляет заменяемые файлы. Запуск требует отдельной разрешённой deployment-операции, это не проверка доступа.

## Перед будущим deploy
- Проверить точный commit, чистоту checkout и соответствие frontend-артефакта исходникам.
- Иметь проверенный backup базы и медиа, план восстановления и миграционный rollback.
- Проверить текущую ревизию и настройки TLS в рамках разрешённого доступа.
- Пройти backend и frontend tests, build, PostgreSQL regression suite.
- Выполнять только после разрешения на deployment и при отсутствии действующего ограничения доступа.
- После: проверить REVISION, миграции, health, worker и изолированный бизнес-сценарий; не выдавать health за полную проверку продукта.

Production, SSH и секретные файлы в этой работе не читались. Наличие backup/restore и фактический TLS не подтверждены.

Источники: [README](https://github.com/zapleoceo/sim/blob/b27a89ba5c7dae31ed48da78d2eefef6db681100/README.md), [backend config](https://github.com/zapleoceo/sim/blob/b27a89ba5c7dae31ed48da78d2eefef6db681100/backend/pyproject.toml), [frontend scripts](https://github.com/zapleoceo/sim/blob/b27a89ba5c7dae31ed48da78d2eefef6db681100/frontend/package.json), [deploy](https://github.com/zapleoceo/sim/blob/b27a89ba5c7dae31ed48da78d2eefef6db681100/infra/deploy.sh), [compose](https://github.com/zapleoceo/sim/blob/b27a89ba5c7dae31ed48da78d2eefef6db681100/infra/docker-compose.yml).
