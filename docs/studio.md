# Private Studio media: dormant wiring

Studio has separate speech/video controls and a service wired to the existing Broker
ports. Production constructs the service with `enabled=False`; there is deliberately
no environment, owner setting or API parameter to enable it. Status reports **disabled,
capabilities not verified**, not a claim that Broker lacks those capabilities.
The UI is not proof of provider availability. Text chat retains its existing gates.

The same owner role AND exact configured personal subscription-owner Telegram ID gate
protect submission, status and artifacts. CSRF protects POST. No owner binding,
credentials, scopes, budgets or incoming-media settings change. No Telegram sends.
Neither the private reference photo nor chat history is automatically sent to a media
provider. Existing photo/video assets remain separate from generated results.

The future service contract accepts at most 2000 characters, fixed speech voice Kore
or a four-second 720p video. These are dormant defaults, not verified provider promises.
Google speech/video are separate potentially paid routes, not the Sonnet/Sol subscription.
There is no paid fallback or automatic retry.

Migration 0037 adds an append-only operation identity journal (owner, UUID, kind,
request hash, creation time, status, encrypted result). No prompt or raw provider error
is stored in it. A committed `unknown` intent precedes a provider call. PostgreSQL's
owner row lock serializes submissions including different UUIDs. Completed IDs reuse
their encrypted result; changed input for the same ID conflicts. Any unresolved intent
blocks new IDs for that owner too. Cancellation, process/connection loss or failed
result storage leave `unknown`; a timeout is not proof of provider rejection. No reset
or delete endpoint is provided. Status reads never generate. Artifacts are private,
no-store and nosniff; the UI labels them synthetic and not sent to clients.

Before activation: independently verify subscription-owner identity and existing-session
acceptance, exact provider capabilities/permissions/cost limits, streaming response bounds,
artifact signature validation and the provider's receipt/reconciliation contract. Current
adapters decode complete responses; service output caps (2 MiB speech, 20 MiB video) are
post-response validation, NOT transport memory bounds. No remote job ID reconciliation
is implemented for these ports; an unknown intent requires investigation and remains
blocked indefinitely. Do not activate by simply flipping the constructor argument.

CI uses fake speech/video only: disabled/default/auth/CSRF, completed reuse, changed
request conflict, encrypted artifact, cross-owner isolation, unknown and cancellation
across service restart, UI no-resubmit. No real provider quality or live visual acceptance
is claimed. Production migration follows existing backup/stop-worker/readiness gates.
