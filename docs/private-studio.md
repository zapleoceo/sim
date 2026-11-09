# Private media studio (partial integration)

The authenticated `/studio` owner screen displays reference photo, preview video,
then subscription chat. Missing assets are explicit; no placeholder is represented
as a user reference or generated result. This change is NOT a completed Google
speech/video integration and does not authorize deployment or paid generation.

## Private subscription routing

`SIM_STUDIO_OWNER_TELEGRAM_ID` defaults to zero (disabled). Configure the verified
personal subscription owner's Telegram ID, not all administrator accounts.
An owner/superadmin role alone is insufficient to use subscriptions or read assets.
Broker must independently allow this project/account and expose `available=true`,
`tested=true`, `billing_type=subscription` Sonnet/Sol entries in `/v1/models`.
The client rechecks that catalog for every chat request, submits the exact model
through `/v1/jobs?capability=chat:fast`, and never chooses a paid fallback.
No new token is created, copied to the browser or committed. Existing
`SIM_BROKER_PROJECT_KEY` remains server-side. Personal subscriptions are not enabled
for product customers or third parties (Broker docs/agent-runs.md scope limitation).

Chat is transient in the current browser page, text-only and bounded to 24 turns /
32000 characters total / 8000 per turn. The assistant explicitly has no photo/video
input. It can draft media prompts but cannot claim to have generated media.

## Reference assets and remaining work

Place only the verified user-provided reference and actual approved video artifact
under `SIM_MEDIA_DIR/studio`. Set `SIM_STUDIO_PHOTO_FILENAME` and
`SIM_STUDIO_VIDEO_FILENAME` to their filenames. Asset endpoints require the bound
personal owner, disallow traversal/symlink escape, accept only image/video formats
and return private/no-store responses. No upload or external URL fetch is added.

The previous reference photo has not been recovered in this implementation.
Existing Broker source does not provide a verified speech/video job contract.
Google model names mentioned by the owner must be verified against live provider
and Broker catalog/contract before implementation. Do not guess routes from ASR
or text generation. Required next steps:

1. Recover and visually verify the original photo, with its provenance.
2. Implement/verify Google TTS and image-to-video jobs in the existing Broker,
   including artifact retrieval, quotas, cancellation and accurate cost handling.
3. Confirm any required photo transmission/payment authorization before live calls.
4. Connect media actions and test with mocked providers in CI.
5. Only then run authorized live acceptance, existing production release gates,
   and browser QA. This PR does not enable those operations.

## Verification

Per AGENTS.md, no local lint, tests, build or typecheck. Backend fake-Broker tests
and frontend HTTP/access tests run only in the existing SIM CI workflow on the
published exact commit. Never report a queued workflow as passed.
