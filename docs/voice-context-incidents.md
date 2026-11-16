# Voice context updates and incident follow-up

The post-PR97 voice gate correctly refused a candidate after its dialog revision changed,
but treated every revision change as a safety handoff. Telegram history polling also accepted
empty service records (including call status records) as conversational messages, creating
unread/reply work and invalidating the review. These are independent bugs: filter service
records while advancing the connector cursor, ignore empty no-media inbound records at
ingest, and distinguish ordinary context cancellation from a safety rejection.

A normal context update cancels that candidate without releasing PCM or setting a persistent
AI pause. Later turns still require a fresh guard review and exclude the unplayed candidate
from their history. Interruptions retain the PR97 epoch fence. Real guard blocks, disabled
AI/calls, errors and review timeouts still fail closed. Call eligibility is now consistent
before opening a provider session and at each review; a pending operator takeover cannot
cause a paid call to open and then immediately fail. No existing production pauses are reset.

Each new assistant transcript row keeps the model/provider reported by the live session;
ready metadata fills missing done metadata. Unknown providers remain unknown rather than
being inferred from a model prefix. Voice reviews have workflow `call_guard` and receive
`context.medium=voice`. Call instructions and guard rules distinguish a direct identity
question (truthful AI disclosure required) from ambiguous references to another person,
and noisy ASR fragments from an explicit language switch. This narrows semantic checking
without suppressing personality or pretending the persona is human.

Older calls saved the broker's end-of-call transcript and stored model metadata on the call
header only. Some historical rows can therefore contain merged/truncated text and missing
per-message model/guard values. PR97 changed to completed, individually reviewed turns and
transport acknowledgements. This change does not rewrite historical transcripts or assert
that a stored transcript proves what a caller heard. No raw old prompt/audio snapshot exists
in SIM for some historical calls; a current prompt reconstruction is not that snapshot.

The UI summarizes voice cancellation in plain Russian, keeps diagnostics expandable, and
derives “AI on/off” from current state instead of historical handoff. A cancelled message no
longer claims the conversation necessarily went to a human. The operator-autopause hint was
already removed in PR96; an old open browser tab can still show its previously loaded bundle.
Reload is needed to load the deployed UI; no claim is made that the browser was refreshed.

Reply-delay journal records now store a UTC instant (`reply_at:<ISO>Z`), rendered in the
user's date/time with an explicit offset. Routine reasons label the persona's timezone.
Historical bare clock strings are explicitly marked as persona time with an unknown stored
zone, never guessed from current settings. Plans use the same zoned date/time format and
plan-only poll updates refresh the card. A planned time is not a delivery guarantee: model
timeouts/retries can delay it, and an outdated plan is not sent.

Regressions run only in CI using fake transports/models: service filtering/cursor, empty
ingest without revision/unread/jobs, empty events during review, real context cancellation,
next-turn review without false handoff, continued hard-block/pause fences, transcript model,
and UI reason/status/timezone boundaries. No production data fixtures, real calls, messages
or paid provider probes are used. Production deployment remains a separate reviewed gate.
