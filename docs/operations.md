# Запуск, тесты и деплой SIM

Кандидат: main `a7736bcb3d493f25be340f064b92f46e9c2bb14b` + [PR #9](https://github.com/zapleoceo/sim/pull/9).
Слияние PR #9 и deployment не подтверждены; это описание подготовленного изменения.

База документации: main `a7736bcb3d493f25be340f064b92f46e9c2bb14b`. Фактическая production-версия неизвестна.

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

Команды выше — инструкция для будущего разрешённого полного прогона, не список всего
выполненного. Для кандидата PR #9 локально: 464 backend-теста прошли (16 существующих
предупреждений Alembic), Ruff прошёл; TypeScript app/spec и ESLint ранее прошли на неизменённом frontend. Компиляция spec
не означает выполнение unit tests. Последняя Angular build не подтверждена: сетевой доступ к npm registry не был разрешён.
Browser/Karma не запускались (известное ограничение среды на сокеты). PostgreSQL locking,
реальные каналы, production health и release commit не проверены. Исторический статический
аудит b27a89b не следует выдавать за эти более поздние результаты.

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

## Публикации Instagram и восстановление

По умолчанию `SIM_IG_CONTENT_ENABLED=false`, `SIM_IG_CONTENT_POST_IDS=[]`. Оба gate,
owner approval и явный активный канал обязательны; deployment сам по себе не разрешает
публикацию. В этой работе флаги не включались. Перед отдельным пилотом прочитайте
[ограничения фото](instagram-content-publishing.md) и [durable intent/receipt](publication-intents.md).
Миграция 0006 нужна до запуска нового кода. Постоянные intents включать в backup/restore;
не удалять записи и не восстанавливать snapshot без новых intents ради повторной отправки.
Downgrade непустой intent-таблицы запрещён. Неизвестный исход проверяется человеком в
реальном аккаунте только при разрешённом доступе, не повторным upload и не копированием
поста под новым ID. Read-only `/api/content/{id}/publication` доступен owner/superadmin.
Receipt может восстановить статус только при совпадении fingerprint и допустимом состоянии
поста/аккаунта; это не подтверждение exactly-once внешнего сервиса.

Production, SSH и секретные файлы в рамках обновления документации не читались.
Наличие backup/restore и фактический TLS не подтверждены. Независимые локальные проверки остаются отдельной задачей.

Источники: [README](https://github.com/zapleoceo/sim/blob/a7736bcb3d493f25be340f064b92f46e9c2bb14b/README.md), [backend config](https://github.com/zapleoceo/sim/blob/a7736bcb3d493f25be340f064b92f46e9c2bb14b/backend/pyproject.toml), [frontend scripts](https://github.com/zapleoceo/sim/blob/a7736bcb3d493f25be340f064b92f46e9c2bb14b/frontend/package.json), [deploy](https://github.com/zapleoceo/sim/blob/a7736bcb3d493f25be340f064b92f46e9c2bb14b/infra/deploy.sh), [compose](https://github.com/zapleoceo/sim/blob/a7736bcb3d493f25be340f064b92f46e9c2bb14b/infra/docker-compose.yml).
