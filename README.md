# SIM v2

Один AI-персонаж (Марина) в личном Telegram-аккаунте: переписка текстом и голосом,
понимание фото, видео и файлов, фото и видео по референсам внешности, живые звонки.
Все модели — через AI Broker.

## Документы

| Документ | О чём |
|---|---|
| [docs/spec-v2.md](docs/spec-v2.md) | ТЗ: что входит, критерий готовности, этапы |
| [docs/context.md](docs/context.md) | Контекст агента и журнал событий |
| [docs/reply.md](docs/reply.md) | Ход персонажа: план ответа, проверка, действия |
| [docs/persona-life.md](docs/persona-life.md) | Жизнь персонажа: распорядок, план дня, истории, канон, занятость, сцена и непрерывность фото и звука |
| [docs/database.md](docs/database.md) | Таблицы и кто их пишет |
| [docs/api.md](docs/api.md) | HTTP API дашборда |
| [docs/auth.md](docs/auth.md) | Вход через Telegram, сессии, CSRF |
| [docs/broker.md](docs/broker.md) | Клиент AI Broker |
| [docs/telegram.md](docs/telegram.md) | Telegram userbot: приём и отправка |
| [docs/calls.md](docs/calls.md) | Звонки |
| [docs/frontend.md](docs/frontend.md) | Дашборд |
| [docs/deploy.md](docs/deploy.md) | Сервисы, переменные окружения, выкат |

## Процессы

| Процесс | Команда | Что делает |
|---|---|---|
| `api` | `uvicorn sim.apps.api:create_app --factory` | Дашборд и HTTP API (Litestar) |
| `worker` | `python -m sim.apps.worker` | Ответы, распознавание, генерация, память (очередь `agent`) |
| `telegram` | `python -m sim.apps.telegram` | Сессия Telegram, приём, отправка, звонки (очередь `telegram`) |

Очередь задач — Procrastinate в той же PostgreSQL. Живые обновления дашборда идут через
`NOTIFY sim_events` → SSE `/api/events`.

## Код

```
backend/sim/
  domain/     перечисления, схема плана ответа, статусы в чате (presence.py), жизнь и сцена
              персонажа (life.py, scene.py)
  ports.py    интерфейсы внешнего мира (Broker, Telegram, хранилище, ffmpeg, часы)
  db/         схема БД и сессии
  events.py   запись в журнал чата
  services/   логика: inbox, понимание вложений, контекст, ответ, проверка, действия, память
  adapters/   Broker, ffmpeg, хранилище файлов
  telegram/   Telethon-аккаунт, отправка, «прочитано» и живые статусы (presence.py), звонки
  api/        Litestar
  tasks/      задачи Procrastinate
  apps/       точки входа процессов
frontend/src  React-дашборд: Чаты, Персонаж, Клиенты
```

Проверка — только CI (`.github/workflows/ci.yml`), см. [AGENTS.md](AGENTS.md).
