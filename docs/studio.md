# Private Studio media: dormant wiring

HTTP audit retains method/path/status/actor, but omits Studio media request bodies.
Artifact response bodies are not traced. Private prompts and binary media are not copied
into the activity request-body viewer.

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

Studio-only transport now streams raw identity-encoded JSON with fixed caps: 3 MiB
speech response, 32 KiB submit receipt, 28 MiB video poll. Content-Length is checked but
not trusted; accumulated bytes are bounded before append, read chunks at most 64 KiB.
Compressed bodies and redirects are rejected, streams close on overflow/cancel/timeout.
Wall limits are 95s speech / 35s submit or single poll, with no submission retry. Base64
length is bounded before decoding (2 MiB speech / 20 MiB video). Existing non-Studio
Broker callers are unchanged. This is bounded buffering, not constant-memory parsing.

Only PCM WAV and MP4 pass Studio validation. WAV requires RIFF/WAVE, exact RIFF size,
PCM16 mono/stereo 8–48kHz and complete frame data. MP4 requires bounded ordinary
top-level boxes, supported ftyp brand, nonempty moov/mdat, at most1024 boxes and no
truncated/extended/zero-size boxes. Checks apply before storage AND before serving.
These are container/signature checks, not full codec validation or malware sandboxing;
some otherwise valid containers are intentionally rejected. No decoder is invoked.

Remote contract evidence: AIbroker commit `df4460a5bbe3a3e7311861e024e1ca1b1c893156`,
`src/aibroker/routes/proxy.py`: `video_submit` returns job_id; `jobs_poll` scopes reads
to project ID. Studio commits the receipt (encrypted ID + hash of Broker URL/project key)
before reading a result, constructs `/v1/jobs/{id}` itself and ignores poll_url. Explicit
CSRF-protected reconciliation performs GET only, refuses scope/key mismatch, and reuses
a completed result. This operation is hard-disabled in production too. Pending, failed,
missing/pruned or unreadable results do not authorize another POST.

**Unresolved remote gap:** video_submit accepts neither Idempotency-Key nor labels;
speech_endpoint is synchronous and returns a usage request_id, not a recoverable media
job. A lost submit receipt cannot safely be discovered or resubmitted. Such an intent
remains blocked. No invented correlation, provider probe or new Broker request is used.

Owner-bound GET history returns up to50 operations, unresolved first, without prompts
or encrypted payloads. History/status queries defer the large encrypted artifact column
instead of loading it and merely hiding it at serialization. UI restores the pending ID after reload; local status checks are
GET-only and remote reconciliation is a separate disabled action. No localStorage is
used. History failure keeps generation blocked; the server still fences all unknown IDs.

Before activation: independently verify subscription-owner identity/session acceptance,
exact deployed provider contract, permissions/cost limits and real artifact compatibility.
Lost-receipt/speech recovery remains blocked as described. Do not activate by simply
flipping the constructor argument. No live quality or complete codec safety is claimed.
Downgrade refuses to discard a nonempty operation journal. Keep receipts on rollback;
only disposable CI explicitly deletes its synthetic history after checking this refusal.

CI uses fake speech/video only: disabled/default/auth/CSRF, completed reuse, changed
request conflict, encrypted artifact, cross-owner isolation, unknown and cancellation
across service restart, bounded streaming/overflow/cancel/timeout, malformed signatures,
known video receipt recovery/scope mismatch, owner history and UI reload/no-resubmit.
No real provider quality or live visual acceptance
is claimed. Production migration follows existing backup/stop-worker/readiness gates.

The Studio history API regression reuses its fixture's authenticated session/CSRF token.
The shared Telegram widget helper derives auth_date from wall seconds minus a counter;
an immediate second login across a one-second boundary can repeat the same signed payload.
Replay rejection is correct and unchanged. Studio endpoint tests must not manufacture a
second login merely to obtain a token they already have. Main run44019802870 stopped
before deploy on that test-only issue; no runtime/auth policy change is required.
