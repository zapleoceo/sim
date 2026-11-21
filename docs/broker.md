# AI Broker и медиа-инструменты (адаптеры)

Код: `backend/sim/adapters/broker/`, `backend/sim/adapters/media_tools.py`.
Тесты: `backend/tests/adapters/`. Интерфейсы — `backend/sim/ports.py`.

Сервисы SIM знают только порты. Всё, что ходит в Broker или запускает ffmpeg, лежит здесь.

## 1. Устройство клиента Broker

Один общий `BrokerHttp` и по одному маленькому классу на порт:

| Класс | Порт | Вызов Broker |
|---|---|---|
| `BrokerLlm` (+ `BrokerModelCatalog`) | `LlmPort` | `POST /v1/jobs?capability=…` → опрос `GET /v1/jobs/{id}`; `GET /v1/models` |
| `BrokerTranscriber` | `TranscriberPort` | `POST /v1/transcribe/keyed-jobs` → опрос `poll_url` |
| `BrokerSpeech` | `SpeechPort` | `POST /v1/speech` (синхронно, WAV в base64) |
| `BrokerImageGen` | `ImageGenPort` | `POST /v1/images/jobs` → опрос; для `codex-sub/*` — `POST /v1/jobs` |
| `BrokerVideoGen` | `VideoGenPort` | `POST /v1/video/jobs` → опрос |
| `BrokerLive` / `BrokerLiveSession` | `LivePort` / `LiveSession` | WebSocket `/v1/live` |

`BrokerAdapters.from_settings(settings)` собирает все адаптеры поверх одного пула
соединений; `aclose()` закрывает его при остановке процесса.

`BrokerHttp` держит:

- базовый адрес и ключ проекта (`X-Project-Key` в каждом запросе);
- один `httpx.AsyncClient` (создаётся при первом запросе, таймауты 5/30/30 с);
- бюджеты ожидания: `poll_budget_s` (настройка `SIM_BROKER_POLL_BUDGET_S`, 300 с) для чата и
  расшифровки и `media_budget_s` (`SIM_BROKER_VIDEO_BUDGET_S`, 900 с) для видео, фото и
  подписочных моделей (`claude-sub/`, `codex-sub/` — они работают как агенты и отвечают
  минутами);
- `submit()` — отправить задание и опрашивать до `done`;
- `poll()` — опрос (Broker не сообщает о готовности сам и не держит запрос открытым):
  первые 90 с задания — раз в секунду (`FAST_POLL_S`), потому что ответ модели обычно готов
  за 3–10 с, а Broker предлагает `poll_after_s` 5–10 с; дальше — пауза из ответа Broker, но
  не больше 15 с. До 5 сбоев опроса подряд терпим.

## 2. Ошибки и повторы

Все адаптеры бросают `PortError` (HTTP-ошибки — его подкласс `BrokerHttpError` с полем
`status`). Флаг `retryable` говорит очереди задач, стоит ли повторить.

| Что случилось | `retryable` |
|---|---|
| Нет соединения, таймаут, обрыв | да |
| HTTP 408, 425, 429, 500, 502, 503, 504 | да |
| Ответ не JSON (страница прокси) | да |
| Задание не закончилось за бюджет | да |
| Больше 5 сбоев опроса подряд | да |
| Задание упало с `error_code` `worker_restarted`, `capacity_busy`, `auth_refresh_failed` | да |
| Остальные HTTP 4xx (400, 401, 403, 404, 409, 413, 422) | нет |
| Задание `error` / `cancelled` / `unknown` с другой причиной | нет |
| Исчерпан бюджет проекта (`budget cap reached`, `request_cap_exhausted`) | нет — до сброса лимита повтор бесполезен |
| Ответ без медиа или с битым base64 | нет — вызов уже оплачен, слепой повтор оплатит его ещё раз |

## 3. Чат (`BrokerLlm.chat`)

Тело `POST /v1/jobs`:

- `model` — закреплённая модель из настроек персонажа (пустая строка — без закрепления);
- `messages` — в формате OpenAI. Сообщение только из текста уходит строкой. Сообщение
  пользователя с файлами — списком блоков. Все форматы блоков собраны в одной функции
  `attachment_block` (`blocks.py`), её и править, если Broker поменяет формат:
  - фото: `{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,…"}}`;
  - видео: `{"type": "video_url", "video_url": {"url": "data:video/mp4;base64,…"}}`;
  - PDF: `{"type": "file", "file": {"file_data": "data:application/pdf;base64,…",
    "filename": "document.pdf"}}`;
  - другие типы — ошибка: DOCX и TXT сервис сначала превращает в текст (`document_text`).
  Файлы допустимы только в сообщениях `user`.
- `response_format` — когда передана JSON-схема: всегда `json_schema` со `strict: true`.
  Принимается и голая схема, и готовый объект `{"name", "strict", "schema"}`; имя схемы
  берётся из `label`.
- `max_tokens` — ограничивается диапазоном Broker 1…16384. **Подписочным моделям
  (`claude-sub/`, `codex-sub/`) `max_tokens` и `temperature` не отправляются**: Broker
  отклоняет любые значения, кроме умолчаний. `temperature` не отправляется никогда.
- `workflow` — `sim-<label>` (и в теле, и в строке запроса): Broker группирует по нему
  расходы и держит кэш промта тёплым.

Выбор `capability`:

1. есть файлы → `vision`;
2. подписочная модель → `chat:smart` (так Broker запускает их как агентов);
3. есть JSON-схема → `structured`;
4. иначе `chat:smart`.

Результат: `ChatResult.text` — поле `text` задания, `usage` — модель, стоимость, токены,
время от отправки до готовности, id задания; `raw` — тело ответа без больших полей
(`files`, base64 медиа).

Схема ответа (`response_format`) уходит в Broker без ссылок: `blocks.inline_refs` раскрывает
`$ref`/`$defs` на месте и убирает `discriminator` (pydantic), `oneOf` остаётся. Подписочные
модели (`claude-sub/*`, `codex-sub/*`) схемы со ссылками отклоняют (`unsupported_for_subscription_model:
json_schema references`), остальным раскрытая схема не мешает.

## 4. Каталог моделей (`list_models`)

`GET /v1/models` → `ModelInfo`: `input_modalities` из `modalities`, `output_modalities`,
`capabilities`, `price`. Поле `available` есть не у всех строк: нет поля — модель считается
доступной, скрывает только явный `false`. Строки без `id` пропускаются. Ответ кэшируется на
30 с; ошибка не кэшируется.

## 5. Расшифровка голосовых

По контракту Broker («Rolling out typed media jobs»):

1. Ключ идемпотентности считается из аудио, имени файла и `workflow`:
   `sim-tr-<sha256>`. Любой повтор той же голосовой отправляет тот же ключ, и Broker
   возвращает уже созданное задание, а не платит за расшифровку второй раз.
2. `POST /v1/transcribe/keyed-jobs` (multipart `file`, заголовок `Idempotency-Key`,
   `?workflow=sim-voice`). Ответ 202 — новое задание, 200 — повтор (иногда сразу `done`).
3. Опрос по `poll_url` (`GET /v1/transcribe/jobs/{id}`).
4. Если ответ на отправку потерян (таймаут, 5xx) — задание ищется по ключу
   `GET /v1/transcribe/jobs/by-key/{key}`. Не нашлось — ошибка с `retryable=True`;
   повтор задачи снова отправит тот же ключ.
5. 404 на `keyed-jobs` — старая реплика во время выката, `retryable=True`.
6. **Никакого отката** на `/v1/transcribe/jobs` или синхронный `/v1/transcribe`: старая
   реплика приняла бы запрос без идемпотентности.

Параметр `model` порта не отправляется: у маршрута нет закрепления модели, Broker сам
идёт по цепочке расшифровки.

## 6. Голос (TTS)

`POST /v1/speech` `{text, voice, style, model, workflow}`; `text` 1…5000 символов,
`style` обрезается до 300. Ответ — WAV 24 кГц моно в base64 (`audio_base64`), длительность
`audio_s`. Таймаут чтения 150 с (у Broker 60 с на попытку ключа). OGG/Opus для Telegram
делает `media_tools.wav_to_ogg_opus`.

## 7. Генерация фото

Основной путь — `POST /v1/images/jobs` (Gemini image, несколько референсов):

```json
{"prompt": "…", "references": [{"image_base64": "<base64>", "mime": "image/jpeg"}],
 "aspect_ratio": "3:4", "workflow": "sim-photo", "model": "gemini/gemini-3.1-flash-image"}
```

Контракт Broker (AIbroker PR #161, scope `llm:image`): до 8 референсов JPEG/PNG/WebP, до 7 МБ
каждый и 20 МБ вместе; `aspect_ratio` из 1:1, 2:3, 3:2, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9;
ответ `202` с `job_id`, готовая задача в `GET /v1/jobs/{id}` несёт `image_base64`,
`mime_type`, `cost_usd`. Модели: `gemini/gemini-3.1-flash-image` (обычный выбор),
`gemini/gemini-3-pro-image` (лучше держит внешность), `gemini/gemini-3.1-flash-lite-image`
(дешевле, хуже с несколькими референсами). Запрос собирает `images_job_body`, ответ читает
`image_of`.

Видео и PDF во входе vision (тот же PR): блоки `video_url` (mp4/webm/mov) и `file`
(`file_data` + `filename`), только inline base64, до 20 МБ на запрос, только Gemini-модели
с `video`/`pdf` в `modalities`.

Запасной путь — модель `codex-sub/*` (как в v1): `POST /v1/jobs?capability=chat:fast`,
текст `$imagegen …`, до 5 референсов блоками `image_url`, без `max_tokens` и без
`no_shell` (он выключает инструмент картинок). Картинка — первый PNG из `files[]`.

Бюджет ожидания — медийный (900 с).

## 8. Генерация видео

`POST /v1/video/jobs`:

```json
{"prompt": "…", "duration_s": 5, "aspect_ratio": "9:16", "resolution": "720p",
 "workflow": "sim-video", "model": "…", "image_base64": "…", "image_mime": "image/jpeg"}
```

`duration_s` ограничивается 3…15. Первый кадр — JPEG, PNG или WebP не больше 4 МБ
(иначе ошибка до запроса; сервис готовит кадр через `image_for_model`). Готовое задание
несёт `video_base64` (MP4). Бюджет — медийный.

## 9. Звонки (`/v1/live`)

Один WebSocket — один звонок, заголовок `X-Project-Key`, User-Agent `sim-broker/2.0`
(Cloudflare не пускает клиента без него).

1. `open()` отправляет `{"type": "start", system, workflow, model, voice, language,
   greeting, max_seconds}` (пустые поля не отправляются) и ждёт `ready` до 45 с.
   Отказ `{"type": "error", code, detail}` → `PortError`; повторяемы коды 408, 429, 5xx
   (в том числе в виде кода закрытия 4000 + статус).
2. `send_audio()` — бинарные кадры 16 кГц моно s16le.
3. `events()` — одна задача-читатель владеет сокетом и раскладывает в очередь
   `LiveEvent`: `audio` (24 кГц PCM), `transcript_in` (говорит клиент), `transcript_out`
   (говорит модель), `interrupted` (клиент перебил — недоигранный звук надо сбросить),
   `error`, `done` (итог звонка). Обрыв сокета даёт `error` и конец потока.
4. `finish()` — отправляет `{"type": "end"}`, ждёт `done` до 15 с, закрывает сокет и
   возвращает итог: `transcript`, `duration_s`, `tokens_in`, `tokens_out`, `cost_usd`,
   `end_reason` (или `{}`, если итога не было). Повторный вызов возвращает то же.

## 10. Медиа-инструменты (`FfmpegMediaTools`)

ffmpeg и ffprobe запускаются через `asyncio.create_subprocess_exec` без оболочки, на
файлах во временной папке (`TemporaryDirectory`, удаляется сразу), с лимитом времени
120 с (по истечении процесс убивается, ошибка повторяемая), из stderr остаётся хвост
400 символов, результат больше 64 МБ отклоняется. Запись файлов и разбор документов идут
в отдельном потоке, чтобы не блокировать цикл событий.

| Метод | Что делает |
|---|---|
| `probe` | Тип по сигнатуре байтов (`filetype`), для фото/видео/аудио — ширина, высота, длительность из ffprobe (поворот телефонного видео на 90° меняет ширину и высоту местами; у фото длительности нет). Если ffprobe не справился — только тип. |
| `wav_to_ogg_opus` | WAV → OGG/Opus 48 кГц моно 32 кбит/с (как пишет сам Telegram). Длительность — из заголовка WAV, иначе ffprobe результата. |
| `to_pcm16k` | Любое аудио/видео → сырой PCM 16 кГц моно s16le. |
| `document_text` | TXT/MD/CSV/JSON — декодирование (UTF-8, затем Windows-1251, затем Latin-1); DOCX — абзацы и таблицы (python-docx); PDF — текст страниц (pypdf). Не больше 30 000 символов, обрезанный текст кончается на `[...]`. Другие типы — `None`; битый файл — `PortError`. |
| `image_for_model` | JPEG/PNG до 2048 px и 4 МБ уходит как есть; остальное (HEIC, WebP, GIF, большие фото) — первый кадр в JPEG, вписанный в 2048 px (не увеличивается). |

## 11. Тесты

- Broker — `respx` вместо сети, поддельные часы (`sleep` двигает время мгновенно):
  успех, ошибки задания, таймаут бюджета, сбои опроса, отображение HTTP-статусов,
  параметры подписочных моделей, выбор `capability`, блоки вложений, идемпотентность
  расшифровки и поиск по ключу, запасной путь Codex для фото.
- `/v1/live` — поддельный WebSocket.
- Медиа — чистые функции и поддельный запускатель процессов; настоящий ffmpeg — на
  крошечных сгенерированных WAV и PNG (`test_media_tools_ffmpeg.py`, пропускается, если
  ffmpeg не установлен; в CI он должен быть установлен).
