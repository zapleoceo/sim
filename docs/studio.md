# Private Studio media

## Private video draft (independent of generation)

Personal owners can explicitly save a video draft while generation is disabled. Migration
0038 stores at most 2000 characters encrypted with the existing SecretBox, separately from
the operation journal. GET/PUT /api/studio/media/draft/video retain the existing owner-role,
exact personal Telegram binding and CSRF protections. Bodies are omitted from HTTP traces,
responses are private/no-store. No provider, job, reconciliation or channel is called.
Save is explicit; unsaved edits are memory-only, with a best-effort browser unload warning.
Storage/conflict errors retain local text, never claim success, and never retry automatically.

Drafts are scoped to the authenticated admin and configured Studio reference filenames/binding.
Studio has no CRM persona selector; changing a reference filename isolates the next draft.
Replacing bytes under the same filename is not a character-version change and cannot be
detected by this scope. Old scopes are retained privately, not silently moved to another image.
The UI clears on account/reference changes and restores only the matching server draft.
Concurrent stale saves return409; deletion retains a revision tombstone to prevent resurrection.
Cancel restores the saved text; delete requires confirmation. Neither creates a job. Enabling
generation in a future reviewed release must still require a separate explicit create command;
this draft feature does not schedule, submit, or send an old draft on availability changes.
CI covers reload/repeated save/cancel/delete, failed storage, encryption, trace omission,
owner/reference isolation, and PostgreSQL concurrent first-save/delete fencing.

HTTP audit retains method/path/status/actor, but omits Studio media request bodies.
Artifact response bodies are not traced. Private prompts and binary media are not copied
into the activity request-body viewer.

Studio speech and video have separate, default-off switches
(`SIM_STUDIO_SPEECH_ENABLED` and `SIM_STUDIO_VIDEO_ENABLED`). Both the status and
submit endpoints use the same per-capability admission. They require the existing
owner gate, the capability switch, a supported transport contract, and a bounded
read of the authenticated project catalog containing its exact API-billed model
and capability. A missing, unavailable or mismatched entry closes that capability.
The catalog is a model registry and does not establish project grants, paid key
eligibility, upstream availability or headroom. Confirm those facts before configuring
a route. Broker enforces scope and budgets when it admits requests. Text chat retains
its existing gates.

The same owner role AND exact configured personal subscription-owner Telegram ID gate
protect submission, status and artifacts. CSRF protects POST. No owner binding,
credentials, scopes, budgets or incoming-media settings change. No Telegram sends.
Neither the private reference photo nor chat history is automatically sent to a media
provider. Existing photo/video assets remain separate from generated results.

The text limit is 2000 characters. The speech adapter pins `gemini/gemini-3.8-flash-tts`
with Kore, and the video adapter pins `gemini/gemini-omni-1.2-flash` for four-second 720p
clips. Studio never chooses another model/provider, auto-submits a draft or sends media
to a channel. These API-billed routes are separate from the Sonnet/Sol subscription.
Speech stays unavailable even with its switch set because the current synchronous
Broker implementation can rotate paid keys following an ambiguous timeout. It needs
a single-attempt or durable dispatch contract before production use.

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
a completed result. It uses the video route's gate. Pending, failed,
missing/pruned or unreadable results do not authorize another POST.

**Unresolved remote gap:** the deployed Broker uses the video endpoint's
`Idempotency-Key` header for Vivix only. Gemini submit has no receipt lookup for a
lost response. Broker retains a durable dispatch record once it accepts a Gemini job
and stops on an uncertain paid result. Studio never resubmits when its own receipt is
missing. Speech returns a usage request ID, with no artifact recovery contract.

Owner-bound GET history returns up to50 operations, unresolved first, without prompts
or encrypted payloads. History/status queries defer the large encrypted artifact column
instead of loading it and merely hiding it at serialization. UI restores the pending ID after reload; local status checks are
GET-only and remote reconciliation is a separate explicit video action. No localStorage is
used. History failure keeps generation blocked; the server still fences all unknown IDs.

Before enabling a supported route, use an authoritative mapping to the existing owner
account, and confirm the Broker project grant, eligible model/key and budget. With
neither owner binding nor a route switch configured, deployment leaves generation off.
An owner may check their first result after activation. CI does not establish live
output quality or complete codec safety.
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
