# Controller retry and chat operations

The generator proposes a complete JSON decision. The pre-send controller receives the reply plus stage, needs, next step, refusal, stop-contact and operator-handoff flags. A controller rejection is sent back to the generator with a short reason; the replacement decision is reviewed again. Neither a blocked reply nor its control fields change client state.

After two rejected or unusable decisions, the dialog is paused for the manager, marked `needs_operator`, and receives one fixed neutral waiting phrase. The rejected draft remains a cancelled, unsent message with controller reasons. A second reply job sees the pause and cannot send a duplicate. Broker/controller outages still fail closed and use the job retry path; they do not authorize an unchecked response. The existing outbound send-intent fence continues to prevent blind duplicate channel sends.

The preview carries its approved stage/status/needs/next-step context between turns. A rejected preview reply never advances that context. Simulation branches likewise apply funnel changes only after the controller approves the reply. Persona prompts may sound natural, but direct questions about whether the speaker is human must never be answered with a false human claim.

The inbox already lists real dialogs across connected channels, supports source/persona/stage filters, text replies, manual stage changes, operator takeover and AI pause. Channels without active sessions cannot deliver real messages. Attachment and voice delivery require explicit channel capabilities and are not inferred from a channel label. No Stepan2 funnel is copied.

Verification is CI-only per `AGENTS.md`. Review the exact PR-head run, including backend/PostgreSQL, frontend and change-gate jobs; no local tests or channel sends.
