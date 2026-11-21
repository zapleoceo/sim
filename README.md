# SIM v2

AI-персонажи (первая — Марина), каждый в своём личном Telegram-аккаунте: переписка текстом и голосом,
понимание фото, видео и файлов, фото и видео по референсам внешности, живые звонки.
Новый персонаж — клонированием, аккаунт подключается из дашборда по QR. О каждом клиенте
персонаж ведёт паспорт (лиды). Все модели — через AI Broker.

## Документы

| Документ | О чём |
|---|---|
| [docs/spec-v2.md](docs/spec-v2.md) | ТЗ: что входит, критерий готовности, этапы |
| [docs/context.md](docs/context.md) | Контекст агента (порядок блоков ради кэша промта) и журнал событий |
| [docs/memory.md](docs/memory.md) | Память: сегодняшний день дословно, заметки дня, ночью итог дня и паспорт, итоги недель, «А помнишь…?» |
| [docs/reply.md](docs/reply.md) | Ход персонажа: план ответа, проверка, действия |
| [docs/agents.md](docs/agents.md) | Агенты персонажа: каждая функция ИИ, её модель, ключи (платные / бесплатные / авто), промт и max_tokens |
| [docs/attention.md](docs/attention.md) | Внимание: когда она берёт телефон и что делает с уведомлениями — ответить сразу, позже, только прочитать; режим, темп, три переключателя |
| [docs/personas.md](docs/personas.md) | Несколько персонажей, «Клонировать», подключение Telegram по QR, архив |
| [docs/leads.md](docs/leads.md) | Лиды, паспорт клиента, стадии, паспортистка, замки полей |
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
| `api` | `uvicorn sim.apps.api:create_app --factory` | Дашборд и HTTP API (Litestar), вход в Telegram по QR |
| `worker` | `python -m sim.apps.worker` | Проверки телефона (Внимание), ответы, распознавание, генерация, ночная память (итоги дней и недель, паспорт клиента), очередь `agent` |
| `telegram` | `python -m sim.apps.telegram` | Сессия Telegram, приём, отправка, звонки (очередь `telegram`) |

Очередь задач — Procrastinate в той же PostgreSQL. Живые обновления дашборда идут через
`NOTIFY sim_events` → SSE `/api/events`.

## Код

```
backend/sim/
  domain/     перечисления, схема плана ответа, статусы в чате (presence.py), жизнь и сцена
              персонажа (life.py, scene.py), реестр агентов и их промты (agents.py,
              agent_prompts.py), настройки и ответ Внимания (attention.py)
  ports.py    интерфейсы внешнего мира (Broker, Telegram, хранилище, ffmpeg, часы)
  db/         схема БД и сессии
  events.py   запись в журнал чата
  services/   логика: inbox, внимание (проверка телефона, attention/), понимание вложений,
              контекст, ответ, проверка, действия, память по дням, паспорт клиента, клонирование
              персонажа
  adapters/   Broker, ffmpeg, хранилище файлов
  telegram/   Telethon-аккаунт, отправка, «прочитано» и живые статусы (presence.py), звонки
  api/        Litestar
  tasks/      задачи Procrastinate
  apps/       точки входа процессов
frontend/src  React-дашборд: Чаты, Персонажи, Лиды
```

Проверка — только CI (`.github/workflows/ci.yml`), см. [AGENTS.md](AGENTS.md).
