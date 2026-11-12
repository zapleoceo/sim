# Controller retry and chat operations

The generator proposes a complete JSON decision. The pre-send controller receives the reply plus stage, needs, next step, refusal, stop-contact and operator-handoff flags. A controller rejection is sent back to the generator with a short reason; the replacement decision is reviewed again. Neither a blocked reply nor its control fields change client state.

After two rejected or unusable decisions, the dialog is paused for the manager, marked `needs_operator`, and receives one fixed neutral waiting phrase. The rejected draft remains a cancelled, unsent message with controller reasons. A second reply job sees the pause and cannot send a duplicate. Broker/controller outages still fail closed and use the job retry path; they do not authorize an unchecked response. The existing outbound send-intent fence continues to prevent blind duplicate channel sends.

The preview carries its approved stage/status/needs/next-step context between turns. A rejected preview reply never advances that context. Simulation branches likewise apply funnel changes only after the controller approves the reply. Persona prompts may sound natural, but direct questions about whether the speaker is human must never be answered with a false human claim.

The inbox already lists real dialogs across connected channels, supports source/persona/stage filters, text replies, manual stage changes, operator takeover and AI pause. Channels without active sessions cannot deliver real messages. Attachment and voice delivery require explicit channel capabilities and are not inferred from a channel label. No Stepan2 funnel is copied.

Verification is CI-only per `AGENTS.md`. Review the exact PR-head run, including backend/PostgreSQL, frontend and change-gate jobs; no local tests or channel sends.

## Operator composer and test chats

Operator text, voice and attachments queue through the existing outbound SEND job and permanent send-intent fence. The UI passes a stable request id on retries; migration 0015 adds a nullable unique per-dialog request key for duplicate POST protection. Operator sends and AI pause acquire the dialog lock used by the reply pipeline. A manual response pauses the AI and cancels queued AI replies and follow-ups. The chat header offers manual stage changes through the existing operator funnel endpoint.

Voice uploads (up to 8 MiB) are converted to OGG/Opus and sent only when the connected port advertises SEND_VOICE: Telegram bot, Telegram user account and WhatsApp. Attachments (JPEG, PNG, WebP, PDF or UTF-8 text up to 8 MiB) are sent only by Telegram bot and Telegram user ports; WhatsApp and Instagram do not advertise that capability. The UI disables unsupported actions and the API also rejects them. These are implemented transport paths, not claims that any external account is connected or a real message has been delivered.

The inbox also lists owner-visible simulation branches with a clear test mark and a read-only transcript. Negative view IDs keep them separate from real dialog IDs. Simulations never call a real channel; continuing a test still uses the existing simulation bot. The new source filter separates tests from channel dialogs; avatar presence, channel, persona and stage filters apply to the visible list. No Stepan2 funnel or channel credentials are imported.
