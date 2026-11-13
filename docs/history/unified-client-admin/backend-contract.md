# Backend contract — unified client admin

Status: proposed implementation contract, based on source `349dc66`, 2026-11-07.
No application code changed during this discovery. This document owns only the contract;
the implementation/plan may refine names consistently across API, frontend and tests.

## Confirmed facts

- `Message` already stores `model`, `provider`, `tokens_in`, `tokens_out`, `cost_usd`.
  `api/schemas.py::message_out` exposes only `model` today. Expose existing values, not
  recalculated nearest-time audit values (`adapters/db/models.py`, `services/outbound.py`).
- `Dialog.client_id` is the correct cross-channel consolidation key. `Client` belongs to one
  persona; ingest may attach identities via an explicit ref or same-persona phone match.
  This task must not merge clients/personas or use a person's display name as identity.
- `Dialog.operator_id` records current takeover, not historical message author.
  `send_manual` receives the authenticated admin ID but currently does not persist it on
  Message. `ingest_own` also creates author=operator messages from the external phone account
  without knowing an admin identity. Existing operator history cannot be backfilled honestly.
- `LlmCall` has optional `dialog_id`, no message ID. Multiple generator retries and guard
  calls can exist for one response; some rows have no dialog (preview/simulation).
  Never attach them to a message by timestamp or sum them into that message's token values.
- PR12 `OutboundSendIntent.sent_at` is a durable acknowledged-send timestamp and can be
  read through an outer join on message ID. It does not exist for legacy/incoming/phone sends.
  `Message.created_at` is source event time for inbound and queued time for outbound, not
  always delivery time. Do not write or remove intent rows from these admin views.
- Existing dialogs/clients/channel listing is available to all authenticated roles; channel
  changes and simulations are owner-only. New message views should preserve this boundary.
  All timestamps are naive UTC in DB, ISO `Z` on the wire via `schemas.dt`.

## Minimum additive schema and capture

Migration `0008` adds nullable `message.operator_id` (FK admin_user, ON DELETE SET NULL) and
`message.operator_name` (String(120), historical display-name snapshot). Existing rows remain
NULL. Stamp both in `send_manual` by loading its already-authenticated operator; do not derive
from `dialog.operator_id`. Preserve snapshot after admin rename/deletion. `ingest_own` remains
unattributed (`operator_id/name = null`). No LLM history backfill or universal event table.

Recommended query indexes: `(message.created_at, message.id)` for global chronological
pagination and `(message.dialog_id, message.created_at, message.id)` for scoped history.
Existing dialog.client_id, message.created_at, llm_call.created_at/dialog_id indexes already
provide a reasonable baseline; avoid claiming measured query gains without EXPLAIN/load tests.

## Shared message serialization (additive)

Keep every existing MessageOut field unchanged; add the following scalar fields consistently
to existing dialog/detail/send responses and new views:

```ts
operator_id: number | null;
operator_name: string | null;
provider: string | null;
tokens_in: number | null;
tokens_out: number | null;
cost_usd: number | null;
```

Named author is known only when operator_name exists. Otherwise render `Оператор · имя не
сохранено`, including external-phone sends. Client and AI author labels can use the associated
client/persona name from the activity context. Tokens/cost are **generator metadata**, not total
cost including guard/retries. Unknown values are null, not zero. Do not fabricate a summed
total when one token component is absent. Guard metadata stays in the existing `guard` object.

New activity item extends this shared payload with context and receipt time:

```ts
interface MessageActivity extends MessageOut {
  dialog_id: number;
  client: { id: number; display_name: string; avatar_url: string };
  persona: { id: number; name: string };
  channel: {
    id: number;
    name: string;
    kind: 'instagram' | 'whatsapp' | 'telegram' | 'telegram_user';
    account_label: string | null;
  };
  sent_at: string | null; // PR12 receipt only; no fallback to created_at
}
interface Page<T> { items: T[]; total: number; limit: number; offset: number; }
```

`MessageOut` retains id/direction/author/text/media_kind/media_url/created_at/delivery/model/error/
guard plus the additions above. For new views, do not add decrypted credentials, raw webhook
payloads, external session data, broker requests or arbitrary job payloads. Existing `error`
text may be legacy provider output: display only generic status in the new list, not raw HTML;
prefer sanitizing/redacting provider credentials before any detail exposure. LLM log below
deliberately returns only `has_error`, never raw provider errors.

## GET /api/activity/messages — authenticated

Response: `Page<MessageActivity>`.

Query parameters (all optional unless noted):

| Parameter | Contract |
|---|---|
| q | Literal substring, trimmed, max 200 chars; search message text OR client display name; escape %, _ and backslash |
| client_id / persona_id / channel_id | Positive integer, exact filter |
| channel_kind | Existing ChannelKind enum |
| direction | `in` / `out` |
| author | `client` / `ai` / `operator` |
| delivery | `queued` / `sent` / `failed` / `cancelled` |
| from / to | ISO datetime or date, same half-open UTC semantics as analytics (`to` date inclusive via next-day bound) |
| limit | Integer 1..200, default 50 |
| offset | Integer >=0, default 0 |

Apply all filters AND; q's internal predicates OR. Order strictly `Message.created_at DESC,
Message.id DESC`. Default no date restriction so all history remains reachable; validate
supplied from < to. Date-only filtering may use the existing reports parser moved into a
shared utility, not copy slightly different date semantics into a second router.
Return 422 for invalid enum/date/range/limit. Empty valid result is 200 with empty items.

Bounded DB plan: one filtered SELECT joining Message→Dialog→Client→Persona/Channel and
outer-joining OutboundSendIntent (unique PK means no duplicates), one count over exactly
the same predicates without ordering. Limit/offset in SQL; no loading whole histories,
no per-message operator/channel/receipt queries. Prefer a dedicated activity repository and
serializer, reused below. Archived channel history must remain visible.

Offset pagination is deterministic on a static dataset but is not a snapshot under new inserts.
UI must reset to page 0 on filter/refresh changes rather than append offset pages while polling;
a future cursor/snapshot contract can add stronger consistency without claiming it now.

## GET /api/clients/{client_id}/messages — authenticated

Same filters except client_id comes exclusively from path, same MessageActivity page and
descending ordering. Verify client existence first (404). No cross-client or cross-persona
rows may leak. Reuse the same query service; do not concatenate each dialog's 1000-row history.
Existing client card/event endpoints remain unchanged. A reply from unified UI must still select
an actual dialog/channel and call the existing protected dialog send endpoint; no ambiguous
"reply to client" mutation is introduced. Pause/unread remain dialog-scoped.

## GET /api/activity/llm — owner/superadmin only

This is a **model-call audit**, separate from the message log. It exposes known persisted calls
including failures and guard calls; it is not a count of customer messages or a webhook log.

```ts
interface LlmActivity {
  id: number;
  created_at: string;
  dialog_id: number | null;
  client: { id: number; display_name: string; avatar_url: string } | null;
  persona: { id: number; name: string } | null;
  channel: { id: number; name: string; kind: ChannelKind; account_label: string | null } | null;
  workflow: string;
  capability: string;
  model: string | null;
  provider: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  cached_tokens: number | null;
  cost_usd: number | null;
  latency_ms: number;
  finish_reason: string | null;
  has_error: boolean;
}
// Response Page<LlmActivity>
```

Filters: `client_id`, `persona_id`, `channel_id`, `channel_kind` as above; `workflow`
(exact string max32), `model` (exact max120), `provider` (exact max64), `has_error` optional
bool, `from/to`, `limit/offset` same rules. Optional `q` (max200) searches model/provider/
workflow literally, not errors or prompt text. Stable order `created_at DESC,id DESC`.
Use OUTER joins: calls without dialog remain visible unless a context filter excludes them.
Never infer missing client/persona/channel from model, workflow or neighbouring call time.
`has_error` reflects nonempty stored error; do not return `LlmCall.error`, requests, prompts,
transport URLs, tokens or job payloads. Return 403 for operator, 401 without authentication.

## What this iteration must not claim

- Message log is a view of persisted messages and **current** delivery states, not an immutable
  history of each retry/transition. LLM audit is per persisted model call. Neither is a raw
  incoming/outgoing webhook request log or a universal operator-action audit.
- No jobs/connector states should be forged into historical events. Channel status/last_error/
  last_activity_at already has a dedicated channel module and can be shown as current state.
- An active channel, green readiness or local demo does not prove successful live delivery.
- Existing clients scoped to separate personas remain separate even for the same human.

## Required regression coverage

1. Same client across at least two channel kinds consolidates, including archived channel;
   another client and another persona do not leak into scoped timeline.
2. Every filter, combined AND filters, wildcard escaping, date/timezone boundaries, stable
   timestamp ties, limits/count/offset and invalid queries; empty and nonexistent-client cases.
3. Named admin send stores a snapshot; later dialog takeover/admin rename does not rewrite
   history; old rows and external phone messages remain null. Admin deletion retains snapshot.
4. Stored model/provider/tokens/cost pass through unchanged; no fabricated zeros or guard totals;
   receipt sent_at is nullable and read-only. Existing send safety and roles unchanged.
5. Authenticated message views, owner-only LLM audit, omitted raw error/credentials/payloads,
   unscoped model calls retain null context and are excluded only by explicit context filters.
