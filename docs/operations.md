# Запуск, тесты и деплой SIM

Срез main `42af69c0bdbb6ee9a24155b5feaca37c2b040b74`, PR #1–#10 слиты.
Подтверждено состояние Git; production-версия и deployment не подтверждены.

Дополнительно описан локальный кандидат fencing состояния job: он ещё не слит.
Кандидат: 502 backend-теста прошли (16 существующих предупреждений, 47,78 с),
63 focused-теста и Ruff прошли. Историческая база PR #10: 470 тестов и Ruff.

База документации: main `42af69c0bdbb6ee9a24155b5feaca37c2b040b74`. Фактическая production-версия неизвестна.

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
выполненного. Последний локальный backend-прогон кандидата: 502 backend-теста прошли (16 существующих
предупреждений Alembic), Ruff прошёл; TypeScript app/spec и ESLint ранее прошли на неизменённом frontend. Компиляция spec
не означает выполнение unit tests. Последняя Angular build не подтверждена: проверка сборки была отклонена из-за риска обращения к npm registry; повторной попытки не было.
Browser/Karma не запускались (известное ограничение среды на сокеты). PostgreSQL locking,
реальные каналы, production health и commit фактического релиза не проверены. Исторический статический
аудит b27a89b не следует выдавать за эти более поздние результаты.

## Проверка nginx login routes

Слитый PR #10 переносит прежний `sim_login` с удалённого `/api/auth/login` на две exact locations:
`/api/auth/telegram` и `/api/auth/link`. Они делят budget `10r/m`, `burst=5`, `nodelay`
по `$binary_remote_addr`; application limiter остаётся отдельной защитой от неудачных
попыток. `/api/auth/config`, `/api/auth/me`, `/api/auth/logout`, UI и другие API используют
общий `sim_api` без изменения rate/burst. Proxy headers и timeout сохранены.

`uv run pytest tests/test_nginx_config.py tests/test_auth.py -q` проверяет source-контракт
конфигурации и приложение. Эти тесты не запускают nginx и не доказывают runtime-поведение.
Локальный offline-прогон PR #10: 36 focused-тестов и все 470 backend-тестов прошли
(16 существующих предупреждений Alembic); Ruff по `sim` и `tests` прошёл.
До отдельно разрешённой установки/reload конфигурации нужны изолированные проверки:
- `nginx -t` с конфигурацией и используемым `proxy_params`.
- Вход через Telegram и link с query string, forwarding headers и session cookie/redirect.
- Общий budget двух login routes, превышение burst, восстановление после паузы и разные IP.
- Обычные config/me/logout и read-запросы не попадают в меньший login budget;
  `/hooks/evolution/` по-прежнему закрыт извне.

В этой работе nginx не запускался, системная конфигурация, TLS и trusted proxy ranges
не менялись. Штатный deploy-скрипт архивирует исходники, но не устанавливает этот файл
в `/etc/nginx/sites-enabled` и не делает nginx reload. Ни публикация кода, ни его deploy
сами по себе не доказывают применение новой nginx-конфигурации.

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

Источники: [README](https://github.com/zapleoceo/sim/blob/42af69c0bdbb6ee9a24155b5feaca37c2b040b74/README.md), [backend config](https://github.com/zapleoceo/sim/blob/42af69c0bdbb6ee9a24155b5feaca37c2b040b74/backend/pyproject.toml), [frontend scripts](https://github.com/zapleoceo/sim/blob/42af69c0bdbb6ee9a24155b5feaca37c2b040b74/frontend/package.json), [deploy](https://github.com/zapleoceo/sim/blob/42af69c0bdbb6ee9a24155b5feaca37c2b040b74/infra/deploy.sh), [compose](https://github.com/zapleoceo/sim/blob/42af69c0bdbb6ee9a24155b5feaca37c2b040b74/infra/docker-compose.yml).

## Блокеры подтверждения релиза

- Production SSH ранее был отклонён; повторных попыток не было. Live-версия, состояние
  миграций и наличие проверенного backup/restore не подтверждены.
- Последняя frontend build не подтверждена: проверка сборки была отклонена из-за риска обращения к npm registry; повторной попытки не было. TypeScript app/spec и ESLint — прежние проверки неизменённого frontend;
  browser/Karma не запускались.
- В доступной среде нет бинарника nginx: `nginx -t` и runtime-проверки не выполнены.
  Source-тесты не заменяют проверку nginx с реальным `proxy_params`.
- `infra/deploy.sh` не устанавливает и не перезагружает nginx-конфигурацию. Слияние кода
  и даже запуск этого скрипта сами по себе не доказывают применение PR #10 на сервере.
- PostgreSQL-конкурентность, origin TLS и реальные каналы не проверены. Публикации
  Instagram выключены по умолчанию; включение не выполнялось.

Эти ограничения нельзя закрыть повторением отклонённых действий через другой маршрут.

## Локальный кандидат job fencing: ограничение rollout

Кандидат ещё не слит; 502 backend-теста, 63 focused-теста и Ruff прошли.
470 тестов/Ruff относятся к исторической базе PR #10.
Схема БД и HTTP API не меняются. Но смешивать старые и новые worker нельзя: старый код
не проверяет поколение claim в complete/fail. До запуска новых процессов нужно прекратить
claim у старых и полностью дождаться завершения либо остановить их текущие job.
Этот порядок требует отдельной разрешённой rollout-операции; здесь ничего не выкладывалось.
Кандидат защищает только состояние очереди, не handler/external side effects и не продлевает
lease. Даже после него неизвестные исходы внешней отправки нельзя считать безопасными для
повторной отправки. Подробности: [контракт кандидата](api.md#локальный-кандидат-fencing-состояния-очереди).
