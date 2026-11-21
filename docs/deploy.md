# Выкат SIM v2: сервисы, переменные, первый переезд с v1

Прод: `hetzner-root`, дерево `/var/www/sim`, compose-проект `sim` (`infra/docker-compose.yml`),
домен `sim.zapleo.com` (Cloudflare → nginx :80). Проверка и обычный выкат — только через CI
(`.github/workflows/ci.yml`), локально тесты не запускаем (`AGENTS.md`).

## Сервисы

Один образ `sim-backend:latest` (`backend/Dockerfile`: Python 3.12, uv, ffmpeg, пользователь
`sim` uid 10001), разные команды:

| Сервис | Контейнер | Команда | Память | Что делает |
|---|---|---|---|---|
| `postgres` | `sim-postgres` | PostgreSQL 16 | 384 МБ | База, очередь Procrastinate, LISTEN/NOTIFY |
| `migrate` | `sim-migrate` | `alembic upgrade heads && python -m sim.seed` | 256 МБ | Схема и начальные данные; завершается до старта остальных |
| `api` | `sim-api` | `uvicorn sim.apps.api:create_app --factory --loop uvloop` | 384 МБ | HTTP API дашборда, `127.0.0.1:8010` → 8000 |
| `worker` | `sim-worker` | `python -m sim.apps.worker` | 768 МБ | Модели, распознавание, ffmpeg (очередь `agent`), генерация (очередь `media`) |
| `telegram` | `sim-telegram` | `python -m sim.apps.telegram` | 512 МБ | Telegram-аккаунт: приём, отправка, звонки. Строго один экземпляр |

Сумма лимитов около 2.3 ГБ (на сервере 7.7 ГБ, общие с другими приложениями).

- Тома: `sim_pgdata` (база), `sim_media` (файлы медиа, `/app/media` у api, worker, telegram,
  migrate). Полные имена в Docker: `sim_sim_pgdata`, `sim_sim_media`; сеть `sim_default`.
- Админка (`frontend/dist/sim/browser`) отдаёт nginx напрямую из `/var/www/sim`, `/api/*`
  проксируется в api. Конфиг — `infra/nginx-sim.conf`, ставится руками (deploy-job его не трогает).
  Для `/api/events` (SSE) выключена буферизация и стоит долгий таймаут.
- Healthcheck: у api — `GET /readyz` (есть связь с БД). worker и telegram HTTP не держат,
  их проверяет smoke деплоя: контейнер должен быть в состоянии `running`.
- Evolution/WhatsApp, Redis и initdb-скрипты в v2 не используются.

### Миграции

Две ветки Alembic (`backend/alembic.ini`):

| Ветка | Где | Что |
|---|---|---|
| `sim` | `backend/migrations/versions` (`0001_initial`, `0002_video_note_generation`, …) | Наша схема, ровно `sim/db/models.py` |
| `procrastinate` | пакет `procrastinate.contrib.alembic` | Таблицы очереди; так рекомендует Procrastinate 3.12 |

- Применять обе: `alembic upgrade heads` (во множественном числе, голов две).
- Новая ревизия SIM: `alembic revision -m "что меняем" --head sim@head`, пишется руками.
- Откат только нашей ветки: `alembic downgrade sim@base` (ревизии Procrastinate необратимы).
- Обновление Procrastinate: новые ревизии приходят вместе с пакетом, `upgrade heads` их
  применит; при выкате без простоя — сначала `pre`, потом `post` (документация Procrastinate).
- CI-гейт `backend/scripts/ci_postgres_smoke.py` на одноразовой базе: `upgrade heads` с нуля,
  сверка autogenerate с моделями (таблицы Procrastinate исключены), значения CHECK у
  перечислений, `downgrade sim@base`, повторный `upgrade heads`. Расхождение = красный CI.
- База v1 для v2 не годится: в `alembic_version` у неё ревизия v1, и `upgrade` остановится с
  ошибкой «Can't locate revision». Это защита: v2 стартует только на чистом томе.

## Переменные окружения

Файл `/var/www/sim/infra/.env` живёт только на сервере, деплой его не перезаписывает.
Compose сам задаёт `SIM_ENV=prod`, `SIM_DATABASE_URL`, `SIM_MEDIA_DIR`, `SIM_STATIC_DIR`.

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `POSTGRES_PASSWORD` | — (обязательна) | Пароль пользователя `sim` в Postgres, из него собирается URL |
| `SIM_ENV` | `dev` | `dev` / `test` / `prod` |
| `SIM_DATABASE_URL` | `postgresql+psycopg://sim:sim@localhost:5432/sim` | Общая для SQLAlchemy и Procrastinate |
| `SIM_PUBLIC_URL` | `http://localhost:8000` | Внешний адрес (`https://sim.zapleo.com`) |
| `SIM_SECRET_KEY` | — | Ключ Fernet или список через запятую (первый шифрует). **Тот же, что в v1** |
| `SIM_MEDIA_DIR` | `/app/media` | Корень медиафайлов (том `sim_media`) |
| `SIM_STATIC_DIR` | `/app/static` | Собранная админка внутри контейнера api |
| `SIM_SESSION_TTL_H` | `72` | Время жизни сессии входа, часов |
| `SIM_COOKIE_SECURE` | `true` | Cookie только по HTTPS |
| `SIM_BROKER_URL` | `https://aib.zapleo.com` | AI Broker |
| `SIM_BROKER_PROJECT_KEY` | — | Ключ проекта SIM в Broker |
| `SIM_BROKER_POLL_BUDGET_S` | `300` | Сколько ждать задачу Broker, секунд |
| `SIM_BROKER_VIDEO_BUDGET_S` | `900` | Сколько ждать генерацию видео, секунд |
| `SIM_BROKER_BATCH_WAIT` | `true` | Одно длинное ожидание на все задания процесса (`docs/broker.md` §2a) |
| `SIM_BROKER_MAX_INFLIGHT` | `{}` | JSON с лимитами одновременных заданий по полосам, поверх значений по умолчанию |
| `SIM_TG_LOGIN_BOT_TOKEN` | — | Бот Telegram Login Widget |
| `SIM_TG_LOGIN_BOT_USERNAME` | — | Его username без `@` |
| `SIM_SUPERADMIN_TELEGRAM_IDS` | — | Telegram ID админов через запятую; `sim.seed` их добавляет и включает |
| `SIM_TG_MIN_SEND_INTERVAL_S` | `1.5` | Минимальная пауза между отправками в Telegram |
| `SIM_CALLS_ENABLED` | `true` | Звонки (выключатель на весь процесс telegram) |
| `SIM_CALL_MAX_SECONDS` | `540` | Максимальная длина звонка |
| `SIM_CALL_RING_MIN_S` / `SIM_CALL_RING_MAX_S` | `3` / `7` | Сколько «звонит» перед ответом |
| `SIM_CONTEXT_BUDGET_TOKENS` | `60000` | Бюджет контекста модели ответов (`docs/context.md`) |
| `SIM_CONTEXT_KEEP_RECENT_EVENTS` | `60` | Сколько последних событий чата всегда в контексте |

Только для CI: `SIM_PG_TEST_URL` (одноразовая база, `postgresql+psycopg://…@127.0.0.1/…`) и
`SIM_PG_DISPOSABLE=YES`. Секреты деплоя в GitHub: `SIM_DEPLOY_SSH_KEY`, `SIM_DEPLOY_KNOWN_HOSTS`,
`SIM_DEPLOY_HOST`, `SIM_DEPLOY_PORT` (окружение `production`).

Данные Telegram-аккаунта Марины (api_id, api_hash, StringSession, прокси) в окружении не
хранятся: они в `channel.credentials_enc`, зашифрованы `SIM_SECRET_KEY`.

## Обычный выкат (CI, push в `main`)

Job `deploy` запускается только после зелёных `backend` и `frontend`:

1. `/usr/local/bin/sim-backup.sh` — свежий бэкап базы (ротируется на сервере).
2. Синхронизация закоммиченного дерева и собранной админки в `/var/www/sim`
   (`infra/.env` и `broker-project.json` сохраняются).
3. `docker compose stop worker telegram` — старые процессы не работают во время миграций.
4. `docker compose build && docker compose up -d --remove-orphans`: сначала `migrate`
   (миграции и seed), только после его успеха — api, worker, telegram; api пересоздаётся.
5. Smoke: `curl 127.0.0.1:8010/readyz`, api/worker/telegram в состоянии `running`, на месте
   `index.html` админки, вывод `alembic_version` и `REVISION`.

### Оборванные задачи

Шаг 3 останавливает воркер и процесс telegram посреди работы: задача, что шла в этот момент,
навсегда остаётся `doing`. Незавершённая проверка телефона не даёт персонажу снова заглянуть в
телефон, а задача с `lock` (паспорт чата, отправка `send:<chat>`) держит все следующие с тем же
замком (2026-11-21 Марина молчала 12 минут после выката; убитая отправка держала бы все
следующие сообщения чата навсегда). Поэтому воркер раз в минуту (`agent.recover_stalled`,
`sim/tasks/stalled.py`) берёт у Procrastinate задачи очередей `agent`, `media` и `telegram`, чей
воркер исчез или перестал слать heartbeat (менеджер задач видит все очереди, поэтому воркер
подбирает и задачи процесса telegram), и:

- ставит заново те, что безопасно запустить дважды (каждая перечитывает состояние и делает
  только недоделанное): ход ответа, разбор медиа, память, паспорт, план дня, проверка телефона,
  периодические тики, а из очереди `telegram` — отправку, «прочитано» и живой статус. Отправка
  повторяется безопасно: `deliver` пропускает сообщение, которое уже не `pending`, а Telegram
  отбрасывает повтор с тем же `random_id`, выведенным из id сообщения
  (`sim/telegram/sending.py`, [telegram.md](telegram.md));
- остальные (генерация в очереди `media` — могла бы заплатить и отправить дважды) помечает
  `failed`: замок освобождается, задача видна в списке. Строку `generation` при этом закрывает
  уборщик (ниже), когда истекает её срок.

### Застрявшие строки

Оборванная задача — не единственный способ застрять: строка коммитится, а задача под неё
ставится отдельным шагом. Процесс может умереть между ними, постановка может упасть, база может
упасть сразу после того, как сообщение ушло в Telegram, воркер может умереть посреди генерации.
Раз в две минуты воркер запускает уборщика (`agent.reap_stuck`, `sim/tasks/reaper.py` →
`sim/services/reaper.py`). Он смотрит на сами строки:

| Что застряло | Признак | Что делает уборщик |
|---|---|---|
| исходящее сообщение | `pending` дольше 5 минут (но не старше суток — суточное не шлём «из ниоткуда») | ставит отправку заново. Ждущая задача поглощает повтор (`queueing_lock=send-message:<id>`), идущая стоит впереди на замке `send:<chat>`, и новая задача пропустит уже отправленное; повтор в Telegram с тем же `random_id` не доставляется дважды |
| генерация `queued` | дольше 5 минут | ставит `agent.run_generation` заново (`queueing_lock=generation:<id>` поглощает, если задача ещё ждёт) |
| генерация `queued` | не началась за 40 минут | `failed` («не началась за 40 мин») |
| генерация `running` | дольше своего срока: фото — 20 минут, видео и кружок — 35 (одно задание Broker до `SIM_BROKER_VIDEO_BUDGET_S` = 15 минут; видео — фото и потом ролик; плюс 5 минут запаса) | `failed` как при ошибке генерации: событие `generation_failed` для оператора, живой статус чата снимается, клиенту ничего |
| ход ответа `running` | дольше 30 минут | `failed` («ход оборвался») — его воркер умер; восстановленная задача делает новый ход |

Срок генерации считается от последней смены статуса (`updated_at`: постановка для `queued`,
взятие воркером для `running`). Окно в 40 минут — одно определение «генерация ещё идёт» для
контекста ответа («не обещай ещё раз, отправится само») и для статуса для команды
(`sim/services/generation_state.py`, [reply.md](reply.md), «Генерация: сроки»).

Постановки после коммита — лучшее усилие (`after_commit`, `sim/services/queue.py`): ход ответа
и генерация ошибку постановки только логируют, поэтому она не переписывает отправленный ход в
`failed` и не роняет готовую генерацию, а строку подбирает уборщик.

### Очереди воркера

Процесс `worker` запускает два воркера Procrastinate (`sim/apps/worker.py`): очередь `agent`
(ответы, проверки телефона, память, планы; до 6 задач параллельно) и очередь `media` (генерация
фото и видео; до 3 параллельно). Несколько видео по 10–30 минут больше не занимают все места
ответов. SIGTERM останавливает оба мягко: каждый доделывает свои задачи в пределах
`stop_grace_period`. Пул соединений Procrastinate у воркера — 6 (по одному на LISTEN каждого
воркера плюс запас), у остальных процессов — 4.

## Первый выкат: переезд с v1

Делается один раз, руками, по шагам. Пока шаги 1–4 не сделаны, PR v2 в `main` не мержим:
иначе CI выкатит v2 поверх базы v1 (migrate упадёт на чужой ревизии, но v1 при этом
остановится). Команды — на сервере под root, если не сказано иное.

### 1. Бэкап v1 → на ноутбук, на сервере не храним

```sh
mkdir -m 700 -p /root/sim-v1-final && cd /root/sim-v1-final
docker exec sim-postgres pg_dump -U sim -d sim -Fc > sim-v1.dump
docker exec sim-postgres pg_dump -U sim -d evolution -Fc > evolution-v1.dump
tar -C /var/lib/docker/volumes/sim_sim_media/_data -czf sim-v1-media.tgz .
cp /var/www/sim/infra/.env env-v1.txt
sha256sum * > SHA256SUMS
```

На ноутбуке (PowerShell):

```powershell
mkdir D:\Backups\sim-v1-final
scp "hetzner-root:/root/sim-v1-final/*" D:\Backups\sim-v1-final\
Get-FileHash D:\Backups\sim-v1-final\* -Algorithm SHA256   # сверить с SHA256SUMS
```

Только после сверки хэшей: `rm -rf /root/sim-v1-final` на сервере.

### 2. Экспорт того, что переносим (JSON → ноутбук)

Нужен образ v2 (в нём `scripts/export_v1.py` и psycopg). Собираем его из коммита PR, не трогая
`/var/www/sim`. На ноутбуке, в папке проекта:

```powershell
git archive <sha коммита PR> | ssh hetzner-root "rm -rf /root/sim-v2-src && mkdir -p /root/sim-v2-src && tar -x -C /root/sim-v2-src"
```

На сервере:

```sh
docker build -t sim-backend:v2-export /root/sim-v2-src/backend
install -d -m 700 -o 10001 /root/sim-v1-export /root/sim-v1-export/media
PW="$(grep '^POSTGRES_PASSWORD=' /var/www/sim/infra/.env | cut -d= -f2-)"
docker run --rm --network sim_default -e PGPASSWORD="$PW" -v /root/sim-v1-export:/out \
  sim-backend:v2-export python scripts/export_v1.py postgresql://sim@postgres:5432/sim /out/v1-export.json
# файлы медиатеки персонажа (они нужны импорту на шаге 5)
docker run --rm -v /root/sim-v1-export:/out sim-backend:v2-export python -c \
  "import json; print('\n'.join(m['storage_name'] for m in json.load(open('/out/v1-export.json'))['media']))" \
  | while read -r name; do cp "/var/lib/docker/volumes/sim_sim_media/_data/$name" /root/sim-v1-export/media/; done
chown -R 10001 /root/sim-v1-export && ls -l /root/sim-v1-export/media
```

Что попадает в JSON (`format: sim-v1-export`, `version: 1`): персонаж id=1 (имя, промт из
`agent_prompt`, голос и интонация, часовой пояс, дата рождения, пол, язык, модель v1 для
справки), `guard_prompt` → будущие `guard_rules`, канал `telegram_user` (с `session_enc` как
есть — зашифрован тем же `SIM_SECRET_KEY`), активные админы с Telegram ID, строки медиатеки
персонажа (вид, имя файла, подпись, теги). Копия JSON — на ноутбук:
`scp hetzner-root:/root/sim-v1-export/v1-export.json D:\Backups\sim-v1-final\`.

### 3. Остановить и удалить v1 вместе с томами

```sh
cd /var/www/sim/infra
docker compose down -v --remove-orphans     # sim-api, sim-worker, sim-evolution, sim-migrate, sim-postgres
docker volume ls | grep -E 'sim_sim_' || echo "томов v1 нет"
docker image rm evoapicloud/evolution-api:v2.3.7 || true
```

`-v` удаляет `sim_sim_pgdata`, `sim_sim_evolution`, `sim_sim_media` — данные v1 остаются только
в бэкапе на ноутбуке и в `/root/sim-v1-export`.

### 4. Поднять v2

1. Дерево v2 из того же коммита PR (как делает деплой, `.env` сохраняется):
   ```sh
   cd /var/www/sim && find . -mindepth 1 -maxdepth 1 ! -name infra ! -name broker-project.json -exec rm -rf {} +
   find infra -mindepth 1 ! -name .env -delete
   cp -a /root/sim-v2-src/. /var/www/sim/
   ```
2. `.env`: оставить `POSTGRES_PASSWORD`, `SIM_SECRET_KEY` (тот же!), `SIM_BROKER_PROJECT_KEY`,
   `SIM_TG_LOGIN_BOT_TOKEN`, `SIM_TG_LOGIN_BOT_USERNAME`, `SIM_SUPERADMIN_TELEGRAM_IDS`,
   `SIM_PUBLIC_URL=https://sim.zapleo.com`, `SIM_COOKIE_SECURE=true`; убрать переменные
   Evolution, Instagram, партнёрки и `SIM_DATABASE_URL` (их v2 не читает или задаёт compose).
3. nginx: сравнить `infra/nginx-sim.conf` с `/etc/nginx/sites-enabled/sim` (сохранить include
   TLS/аудита, если они там есть), заменить, `nginx -t && systemctl reload nginx`.
4. `cd /var/www/sim/infra && docker compose up -d postgres` — пустая база на новом томе, чтобы
   первый CI-деплой смог сделать бэкап.
5. Мержим PR v2 в `main` → CI: бэкап (пустой базы), сборка, миграции, seed, api, worker,
   telegram, smoke. Дождаться зелёного `deploy`.

До импорта telegram не подключается: каналов в базе ещё нет.

### 5. Импорт

```sh
cd /var/www/sim/infra
docker compose run --rm --no-deps -v /root/sim-v1-export:/import:ro api \
  python scripts/import_v1.py /import/v1-export.json /import/media
docker compose restart telegram
docker compose logs --tail 50 telegram
```

Импорт идемпотентен (повторный запуск обновит те же строки и пропустит уже загруженные файлы):
- персонаж: тот, что создал seed («Марина»), получает имя, промт, голос, пояс, дату рождения,
  пол, язык и `guard_rules`; модели агентов остаются по умолчанию — выбираются на странице
  персонажа в «Агентах» из каталога Broker ([agents.md](agents.md));
- канал `telegram_user`: сессия v1 расшифровывается и перешифровывается в формате v2
  `{"api_id", "api_hash", "session", "proxy"}`; статус `active` → `connected`;
- админы: по Telegram ID, активны;
- медиатека: файлы копируются в `sim_media` (`library/<год>/<месяц>/…`), `source=library`,
  фото помечены «референс внешности».

После проверки: `rm -rf /root/sim-v1-export /root/sim-v2-src && docker image rm sim-backend:v2-export`.

**Жизнь персонажа** ([persona-life.md](persona-life.md)) из готового JSON (`{"life": {...},
"arcs": [...]}`), идемпотентно — мир заменяется, истории обновляются по названию:

```sh
docker compose run --rm --no-deps -v /root/life.json:/import/life.json:ro api \
  python scripts/load_life.py /import/life.json --persona-id 1
```

Планировщик (агент «Планировщик», по умолчанию подписочная `codex-sub/gpt-6.1-sol`) сам
спланирует сегодня и (с 03:00) завтра; модель и промт меняются в «Персонаж → Агенты».

### 6. Приёмка (ТЗ, п. 2)

Клиенты v1 не переносятся. Новый клиент создаётся с выключенным ИИ: после первого сообщения
Димы включить ему ИИ в разделе «Клиенты». Затем Дима пишет Марине в Telegram и проверяет:

- [ ] на текст отвечает текстом;
- [ ] понимает голосовое (расшифровка видна в чате) и отвечает на него;
- [ ] отвечает голосовым;
- [ ] понимает вложение: фото, видео, файл (PDF, DOCX, TXT);
- [ ] отвечает фото: готовым из медиатеки, если подходит, или сгенерированным по референсам;
- [ ] отвечает видео, сгенерированным по референсам;
- [ ] каждый ответ прошёл проверку (вердикт виден у хода персонажа);
- [ ] в дашборде видно переписку, медиа, что ушло в модель и что вернулось; новые сообщения
  появляются без перезагрузки (SSE через nginx).

- [ ] Дима звонит Марине: она отвечает голосом, знает переписку; после звонка карточка с расшифровкой появляется в чате.
