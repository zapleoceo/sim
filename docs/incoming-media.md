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

Video preparation now samples exactly five ordered frames from MP4/WebM, at most
5 MiB, 60 seconds, 1920x1080 input pixels. Frames are scaled to at most 640x640,
1 MiB each and 2 MiB together. Decoder commands use fixed argv, private temporary
paths, file-only input protocols, one decoding thread, allocation and time limits.
No script/macros or embedded instructions are executed. The result explicitly
states that only selected frames were interpreted and audio was not transcribed.
CI includes an offline synthetic color video, without a model or channel call.

This increment is not connected to the queue and does not claim incoming
media is enabled in production. Remaining implementation: bounded video frame
acceptance, durable recognition status/context and queue integration, captions,
download limits, race handling and API presentation. Subscription raw audio is
not supported by the current Broker contract; do not silently use the existing
paid transcription endpoint to claim subscription speech support. PDF/Office
support also requires a separately bounded extraction path.

Tests use synthetic bytes and mocked Broker calls. Run only through SIM CI on
the published commit, with no real channel sends, accounts or provider probes.
