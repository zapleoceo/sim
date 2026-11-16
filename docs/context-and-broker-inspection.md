# Context and broker inspection

The persona page retains the existing prompt map and adds an owner-only inspector of saved
persona/strategy/format instructions, voice/media code rules, guard including mandatory facts,
rewrite recheck, retry, passport and summary instructions. The optional dialog selector accepts
only a dialog belonging to that persona. It assembles the current text/voice context without
calling a model or refreshing memory. This is explicitly not a historical sent request; changing
selection uses a distinct query key and never substitutes another dialog's cached data. No
credential settings are included. Sanitized/truncated content is labelled.

The existing owner-only trace drawer now shows available broker request and response bodies,
formatted JSON, copying and honest historical absence. Mobile uses a full-width right drawer;
the existing Sheet and URL trace selection preserve Escape/close/back navigation. New audited
chat calls capture their actual adapter POST body/query and terminal response (including failed
jobs), not a reconstruction from current settings. Intermediate poll bodies, headers and binary
audio are not retained. Old calls and other media transports can lack full payloads and are
labelled accordingly. Nothing is backfilled as if it were historical evidence.

Payloads use the existing trace JSON/retention lifecycle: no migration or new access rights.
Server redaction applies before persistence and again at API projection; payloads are omitted
from bulk log lists. Headers, credential keys, bearer/basic tokens, private keys and embedded
JSON credentials are masked. Known broker project-key echoes are removed by the adapter.
Each direction is bounded to 64 Ki characters, 2000 nodes, depth 12 and 500 items per container;
truncation is explicit. Audit failures continue to emit diagnostics rather than blocking chats.
Only the owner can fetch payloads or dialog-specific context. Copy receives that same safe API
projection; there is no raw export path. Synthetic security/API/component regressions run in CI
only. Live screenshots and real provider/call validation are not claimed.
