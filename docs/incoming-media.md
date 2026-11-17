# Incoming media recognition

Opt-in owner setting `incoming_media_subscription` defaults to **0**. With value
1, new incoming Telegram bot/user attachments enter the existing `media` queue;
reply jobs wait for its settlement. Old jobs retain their original mode. Turning
it off stops queued subscription interpretation; it never reroutes that job to
paid transcription. Legacy mode's existing voice processing is unchanged.
No setting is activated by a migration or deployment.

The recognizer pins `codex-sub/gpt-6.1-terra`, capability `chat:fast`, workflow
`incoming_media`. Every call checks this project's authenticated catalog for
subscription billing, availability and text/image capabilities. No grants,
paid fallback, raw audio inference or persona reply-model changes are made.
Broker source `df4460a5bbe3a3e7311861e024e1ca1b1c893156`,
`src/aibroker/providers/job_contract.py` (read 2026-11-17), permits subscription
text/image data URLs, with a 3,000,000-byte prompt maximum. This is a contract
check, not live quality/latency or upstream capability proof.
The same source validates `no_shell` only for codex-sub without tools; its CLI
disables shell features. SIM explicitly sends `no_shell=true` for this workflow
and rejects other provider routes before POST. No tools are attached.

Limits:
- Downloads: 5 MiB and 45 seconds; Telegram bot streams with a byte cap,
  Telegram user writes into a capped sink (including missing/incorrect size).
  Other adapters without a bounded-download method report unsupported.
- JPEG/PNG/WebP/GIF signatures: 1 MiB; Broker additionally decodes/checks images.
- Inert UTF-8 plain-text documents: 64 KiB, no binary/control-character inputs.
- MP4/WebM: 60 seconds, 1920x1080 pixels, five ordered frames at most 640x640,
  1 MiB total. Fixed ffmpeg argv, private temporary paths, file-only input
  protocols, thread/allocation/time limits. Audio is explicitly NOT transcribed;
  unsampled events are unknown. Base64 plus prompt fits the 2 MB durable journal.
- Raw audio/voice, PDF and Office: explicit unsupported; no paid substitution.
- Output: 12,000 characters, explicit truncation marker. Provider truncation
  reasons fail closed rather than being presented as complete.

`message.media_status`: pending/done/unsupported/failed (null for legacy).
`media_reason` contains a bounded diagnostic code, not raw provider errors.
API and inbox expose state. Caption remains in `message.text`; successful
interpretation is appended as attachment data, not instructions. Failure leaves
an honest marker so the agent cannot claim to have read/heard the attachment.
Only the same message is finalized under the dialog lock; duplicate completion
is ignored, newer message previews survive, and existing pauses/opt-outs are not
changed. A deleted message is not recreated. There is no channel send here.

Broker calls use PR117's encrypted durable journal and worker generation fence.
An unknown POST is reconciled before another call; terminal errors are retained
(`retry_terminal=False`), no fallback/rebuy is introduced. Binary data URLs are
redacted from audit before persistence/API. Accepted request bytes in the private
recovery journal are encrypted, not exposed by activity APIs. Local downloaded
media uses the existing authenticated media route. Exhausted processing leaves
failed state without handoff or pause. Unknown remote outcomes remain in the
journal: failed recognition is not a claim that Broker cancelled an accepted job.

Synthetic CI tests cover queue/caption/context/status, bounds, unsupported input,
pause preservation, duplicate completion, newer previews, unknown POST recovery,
redaction, UI labels and offline generated video. No private fixtures, live model
probes, real channel sends or local test/build/lint execution. Production
activation and live quality remain separate from implementation acceptance.
