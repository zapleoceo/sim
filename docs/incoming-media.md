# Incoming media recognition (implementation in progress)

`SubscriptionMediaRecognizer` is a bounded, subscription-only recognition boundary.
It uses the project's authenticated Broker catalog and pins
`codex-sub/gpt-6.1-terra` on `chat:fast`, workflow `incoming_media`. It does not
change the persona's reply model, grant scopes, or fall back to paid APIs.

The 2026-11-16 production catalog advertises text and image data URLs for that
model, JSON output and client tools; per-token subscription price is zero,
excluding the existing subscription fee. Catalog availability is not a live
inference test (`tested=false`). No live inference was performed for this work.

Current boundary accepts JPEG/PNG/WebP/GIF signatures (maximum 5 MiB; Broker
fully decodes images and enforces its own pixel/single-frame limits) and UTF-8
plain-text documents (64 KiB). Documents remain inert prompt data. Unsupported
binary documents, PDF, audio and raw video fail explicitly before inference.
Results are bounded to 12,000 characters with an explicit truncation marker.

This first increment is not connected to the queue and does not claim incoming
media is enabled in production. Remaining implementation: bounded video frame
extraction, durable recognition status/context and queue integration, captions,
download limits, race handling and API presentation. Subscription raw audio is
not supported by the current Broker contract; do not silently use the existing
paid transcription endpoint to claim subscription speech support. PDF/Office
support also requires a separately bounded extraction path.

Tests use synthetic bytes and mocked Broker calls. Run only through SIM CI on
the published commit, with no real channel sends, accounts or provider probes.
