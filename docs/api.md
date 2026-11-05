# Sim — HTTP API (контракт админки)

Базовый путь `/api`. JSON, даты ISO-8601 UTC. Авторизация — cookie `sim_session` (httpOnly,
Secure, SameSite=Lax) + заголовок `X-CSRF-Token` на POST/PUT/PATCH/DELETE (значение из
`GET /api/auth/me` → `csrf_token`). Ошибки: `{"detail": "<текст>"}` с кодом 4xx/5xx.
Списки: `{"items": [...], "total": N}`, параметры `limit` (≤200, по умолчанию 50), `offset`.

## Auth
| Метод | Путь | Тело / параметры | Ответ |
|---|---|---|---|
| POST | `/api/auth/login` | `{username, password}` | `{user, csrf_token}` + cookie |
| POST | `/api/auth/logout` | — | `204` |
| GET | `/api/auth/me` | — | `{user: {id, username, role}, csrf_token}` / `401` |

Роли: `owner` (всё), `operator` (диалоги, клиенты, воронка; без настроек персонажей/каналов).

## Персонажи
| Метод | Путь | Тело | Ответ |
|---|---|---|---|
| GET | `/api/personas` | — | `{items: Persona[]}` |
| POST | `/api/personas` | `PersonaIn` | `Persona` |
| GET/PUT/DELETE | `/api/personas/{id}` | `PersonaIn` | `Persona` / `204` |
| POST | `/api/personas/{id}/preview` | `{message, history?: [{role, text}]}` | `{reply, decision, model, latency_ms}` — пробный ответ без отправки; `decision` = разобранный JSON-ответ модели `{reply, stage, needs, next_step, handoff_to_operator, refused, stop_contact}` (или `null`, если не разобрался) |

`Persona`: `{id, name, age, gender ("female"|"male"|"other"), avatar_url, tagline, character, backstory, communication_style,
language, model, voice_id, strategy_id, is_active, disclosure_text, created_at}`.
`model`: `""` = цепочка Broker `chat:sales`; иначе точный id (`claude-sub/claude-sonnet-5-5`).

## Стратегии (сценарии общения)
| Метод | Путь | Ответ |
|---|---|---|
| GET/POST | `/api/strategies` | `{items: Strategy[]}` / `Strategy` |
| GET/PUT/DELETE | `/api/strategies/{id}` | `Strategy` |

`Strategy`: `{id, name, goal, offer, partner_name, registration_url, stage_playbook: {<stage>: string},
rules: string[], followup_hours: number[], reply_delay_s: [min, max], created_at}`.

## Модели Broker
| GET | `/api/models` | `{items: [{id, name, subscription, available}]}` — проксирует каталог Broker |

## Каналы
| Метод | Путь | Тело | Ответ |
|---|---|---|---|
| GET | `/api/channels` | — | `{items: Channel[]}` |
| POST | `/api/channels` | `{kind, persona_id, name}` | `Channel` |
| DELETE | `/api/channels/{id}` | — | `204` (отвязывает сессию) |
| POST | `/api/channels/{id}/connect` | по kind: telegram `{bot_token}`; instagram `{username, password, proxy?}`; whatsapp `{}` | `ConnectState` |
| POST | `/api/channels/{id}/verify` | instagram `{code}` (2FA / challenge) | `ConnectState` |
| GET | `/api/channels/{id}/state` | — | `ConnectState` |

`Channel`: `{id, kind, name, persona_id, status, account_label, last_error, last_activity_at}`.
`status`: `new | pending | active | challenge | expired | error`.
`ConnectState`: `{status, qr_data_uri?, need?: "2fa"|"challenge", message?}`.

## Диалоги (единый инбокс WA + TG + IG)
| Метод | Путь | Параметры / тело | Ответ |
|---|---|---|---|
| GET | `/api/dialogs` | `channel_kind?, persona_id?, stage?, status?, q?, unread_only?, needs_operator?` (только диалоги, где ИИ передал/не справился) | `{items: DialogRow[], total}` |
| GET | `/api/dialogs/{id}` | — | `{dialog, client, messages: Message[]}` |
| POST | `/api/dialogs/{id}/messages` | `{text}` | `Message` (оператор; ИИ этого диалога ставится на паузу) |
| POST | `/api/dialogs/{id}/ai` | `{paused: bool}` | `Dialog` (пауза отменяет ещё не отправленные ответы ИИ; снятие паузы сбрасывает `needs_operator`) |
| POST | `/api/dialogs/{id}/resolve` | — | `Dialog` — снимает `needs_operator`, не меняя паузу ИИ. Флаг также снимается ответом оператора и возобновлением ИИ |
| POST | `/api/dialogs/{id}/read` | — | `204` |

`DialogRow`: `{id, channel_kind, persona: {id, name, avatar_url}, client: {id, display_name, avatar_url, stage, status},
last_message: {text, direction, at}, unread: number, ai_paused}`.
`Message`: `{id, direction: "in"|"out", author: "client"|"ai"|"operator", text, media_kind?, media_url?, created_at, delivery: "queued"|"sent"|"failed"|"cancelled", model?}`. `cancelled` — ответ ИИ не отправлен, потому что диалог перешёл к человеку (оператор/владелец написал, пауза ИИ) или клиент отказался.

## Клиенты
| Метод | Путь | Тело | Ответ |
|---|---|---|---|
| GET | `/api/clients` | `stage?, status?, persona_id?, q?` | `{items: Client[], total}` |
| GET | `/api/clients/{id}` | — | `ClientCard` |
| PATCH | `/api/clients/{id}` | `{notes?, next_step?, next_step_at?, display_name?}` | `Client` |
| POST | `/api/clients/{id}/stage` | `{stage?, status?, reason?}` | `Client` (оператор; `registered`/`paid` вручную запрещены → 422) |

`Client`: `{id, display_name, avatar_url, persona_id, stage, status, next_step, next_step_at, notes,
needs: string[], channels: [{kind, dialog_id}], last_contact_at, created_at}`.
`ClientCard`: `Client + {events: FunnelEvent[], partner_events: PartnerEvent[]}`.
`FunnelEvent`: `{id, created_at, type: "stage"|"status", from_stage, to_stage, from_status, to_status, actor: "ai"|"operator"|"partner"|"system", reason}` (для `type=stage` заполнены `*_stage`, для `status` — `*_status`, остальные `null`).
`PartnerEvent`: `{id, event: "registration"|"payment", occurred_at, amount, currency, payout, external_id}`.

## Воронка
| GET | `/api/funnel` | `persona_id?` | `{stages: [{stage, count, clients: ClientMini[] (до 50)}], statuses: {active, paused, no_response, refused}}` |

## Контент Instagram
| Метод | Путь | Ответ |
|---|---|---|
| GET/POST | `/api/content` | `{items: ContentPost[]}` / `ContentPost` |
| PUT/DELETE | `/api/content/{id}` | `ContentPost` |
| POST | `/api/content/{id}/approve` | `ContentPost` (только owner) |
| POST | `/api/content/{id}/generate-caption` | `{caption}` — подпись в стиле персонажа через Broker |

`ContentPost`: `{id, persona_id, channel_id, kind: "photo"|"video"|"reel"|"story", caption, media_urls,
status: "draft"|"approved"|"scheduled"|"published"|"failed", scheduled_at, published_at, error}`.

## Аналитика
| GET | `/api/analytics/summary` | `from, to` (`YYYY-MM-DD`, обе границы включительно), `persona_id?` | см. ниже |

```json
{"contacts": 0, "new_contacts": 0, "replied": 0,
 "by_stage": {"first_contact": 0, "...": 0},
 "registrations": 0, "payments": 0, "revenue": 0.0, "partner_payouts": 0.0,
 "conversion": {"contact_to_registration": 0.0, "registration_to_payment": 0.0},
 "llm": {"calls": 0, "cost_usd": 0.0, "tokens_in": 0, "tokens_out": 0},
 "by_channel": [{"kind": "telegram", "contacts": 0, "registrations": 0, "payments": 0}],
 "daily": [{"date": "2026-11-05", "contacts": 0, "registrations": 0, "payments": 0}]}
```

## Партнёр
| POST | `/api/partner/import` | multipart CSV `client_ref,event,amount,currency,payout,occurred_at,external_id` | `{imported, skipped, errors[]}` |
| POST | `/hooks/partner` | `{client_ref, event: "registration"|"payment", external_id, amount?, currency?, payout?, occurred_at}`; заголовки `X-Sim-Timestamp`, `X-Sim-Signature: hex(hmac_sha256(secret, ts + "." + body))` | `{ok: true}` |

`client_ref` — ref-код клиента, который ИИ подставляет в ссылку регистрации (`?ref=<code>`).

## Webhooks каналов
| POST | `/hooks/telegram/{channel_id}` | Update Bot API; заголовок `X-Telegram-Bot-Api-Secret-Token` |
| POST | `/hooks/evolution/{channel_id}` | событие Evolution `messages.upsert`; заголовок `apikey`; только из docker-сети |

## Служебное
| GET | `/api/health` | `{ok, db, broker, version}` |

## Дополнения реализации (аддитивные поля)
`DialogRow.needs_operator` (в админке — бейдж «нужен оператор» в списке и в шапке диалога), `Dialog` (`/api/dialogs/{id}` → `dialog`: `{id, channel_id, channel_kind, persona_id, client_id, external_thread_id, ai_paused, needs_operator, operator_id, unread, created_at}`),
`Client.ref_code`, `PUT /api/content/{id}` от оператора (не owner) для approved/scheduled поста возвращает его в `draft`, `Strategy.notes`, `Channel.name`. Медиа сообщений отдаётся через `GET /api/media/{name}` (нужна сессия).
`GET /api/dialogs` фильтр по каналу — `channel_kind`. Заголовок `X-Silent` сервер игнорирует.
