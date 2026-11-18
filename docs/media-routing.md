# Dormant media routing, 2026-11-17

Owner preferences studio_photo_subscription_model, studio_photo_google_model and
studio_video_model use existing settings persistence, validation, owner/CSRF and reset.
Defaults empty. They never activate Studio, change persona photo tools or issue a POST.
The settings picker labels empty as disabled, never as automatic Broker selection.
The personal-owner Studio panel is a code inventory, NOT a Broker health/rights check.
Google image namespace validation is syntactic: its generation port does not yet exist.

| Operation | Intended route | Remaining boundary |
| --- | --- | --- |
| Photo | Codex subscription PNG export → Google | Verified model/account/project policy; Google generation port absent. Vision only analyzes. |
| Circle | Google Omni → existing SIM packaging | Video key, budget and durable receipt unconfirmed; AI marking retained. |
| Video | Owner-selected Omni candidate | Only current Broker video contract; no proven quality winner or automatic Vivix fallback. |
| Call | ElevenLabs voice → Vivix avatar | Adapters, RTC bridge and Telegram video-call bridge not confirmed. |

Production Broker image ee43c095225c11d08d0944cf0edd9523a6307d21d368369f170bbcec1a1f37f1
read 2026-11-17: providers/gemini_video.py uses /v1beta/interactions and
gemini/gemini-omni-1.2-flash, 3–15s, 360p/720p, 9:16/16:9; explicitly not live verified.
Its parser reads outputs/steps.content. The official REST response schema distinguishes
steps[].content[] from SDK output_video; this difference is NOT a demonstrated defect.
Broker's separate synthetic contract tests do not prove live entitlement or output quality.
No Broker edits, paid probes, credentials, grants, budget or production changes here.

Official sources checked 2026-11-17:
- https://aistudio.google.com/docs/video — Veo 3.1 Preview retired October 22; Omni replacement.
- https://aistudio.google.com/docs/omni — Interactions and URI delivery.
- https://aistudio.google.com/docs/pricing — Omni $12/M video output tokens; approximately
  $0.023/s 360p or $0.07/s 720p plus input. Five seconds 360p about $0.116 output,
  not a reservation/quote. No requests made.
- https://docs.vivix.ai/video-generation/world-video-generation — W1 candidate, November
  $0.005/s base plus references, own credits. Lower price does not prove quality or rights.
- https://docs.vivix.ai/streaming-avatar/integrate/pure-rendering — external audio into
  TRTC/Agora RTC, avatar video out; not a Telegram bridge.
- https://elevenlabs.io/docs/eleven-api/guides/how-to/speech-engine/livekit-integration —
  voice WebSocket/PCM to LiveKit, not direct Vivix compatibility.

Future call: transcript/context → guarded truthful reply → ElevenLabs audio → Vivix avatar
→ verified channel bridge. Require bounded buffers, barge-in cancellation of audio/video,
actual delivery receipts, consent, AI indication, scoped sessions and teardown. Technical
faults remain technical events, not operator handoff. No session endpoints added here.

fallback_candidate is an unconnected future precondition, NOT execution permission.
Only terminal reconciled nonacceptance due to unavailability with verified target and
authorized budget qualifies. Unknown/timeout/lost receipt/pending/completed/ordinary failed,
budget/safety/permission denials cannot. HTTP status alone proves nothing. Before wiring it,
require durable journal, idempotency, current-context and send fencing. Speech lost receipts
remain blocked. No automatic replay after availability returns.

CI synthetic coverage: preference persistence/reset, validation, owner/CSRF/personal gate,
inactive defaults, no inference, fallback evidence combinations, expandable UI. Live visual
acceptance, model quality and activation are not claimed.

### Existing Telegram video-note packaging: bounded normalization

The existing VideoNoteEncoder is hardened, not a new endpoint or generation route.
Input is structurally checked MP4 up to 20 MiB, finite positive duration <=60 seconds,
up to 1920x1080 pixels. Unsupported/malformed/over-limit inputs fail closed.
One video and optional first audio stream are centre-cropped to 384x384 H.264/yuv420p,
30fps, AAC mono 64k; visible AI disclosure remains. No extra audio is added.
Output is streamed as fragmented MP4 (index first), capped before accumulation at 8 MiB;
it is probed again for format and retained audio before any caller can store/send it.
The shared Linux runner caps stderr/probe at16KiB, child address space512MiB,
CPU30s/wall45s per encode (probe wall8s), stack2MiB, file writes0, descriptors32;
clean environment, fixed binaries, process-group kill/reap on overflow/timeout/cancel.
Existing frame decoding retains its stricter CPU6s/wall8s/1MiB profile.
Resource containment is NOT a filesystem/network namespace sandbox or codec safety proof.
Protocol allowlist excludes network; private temp files are removed on cancellation.
Slow/complex clips can be rejected within these limits. Audio is re-encoded, not bit-exact.
CI uses generated color/sine fixtures, malformed metadata/output and cancellation tests;
no provider inference, real Telegram sends or live compatibility/visual acceptance.
Studio activation, owner/grants/budgets and remote receipt gates remain unchanged.
