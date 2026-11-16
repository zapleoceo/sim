# Contextual continuation after silence

The worker checks silent chats every 15 seconds. Five minutes since the last message is the
analysis threshold, not a delivery deadline. One versioned proposal lives on each dialog:
exact text, offset-qualified time, reason, status and context revision. The model may continue
the current topic, use a relevant passport topic, choose a later time, or decline to write.
It receives all available delivered messages, the saved summary, journal, passport and lead
data. No automatic `no_response` transition or repeated fixed-hour ladder follows the proposal.
Old pending ladder jobs are retired by their handler.

The ordinary routine rules can postpone the selected time. AI pause, operator handoff,
inactive client/persona/channel, opt-out, and the 24-hour WhatsApp/Instagram conversation window
block proactive delivery. Unknown provider restrictions still fail through the transport.
Confirmed delivery updates the inbox preview, message time and activity revision from the
durable send receipt's server timestamp. Five minutes after delivery, the updated conversation
is assessed again, including the previous contact. This assessment may choose `no_send`.
Code permits only one proactive contact until a new delivered human message arrives; even a
model proposing another message cannot create an unsolicited ladder. Context changes permit
reassessment but do not reset this contact budget. Repeated sweeps coalesce the completed assessment.

Every message invalidates the context revision; edits and phone-account outgoing messages do
too. Existing proposals are revised rather than duplicated. Before transport, the text is
reviewed with the actual server time, then the dialog lock serializes a final eligibility and
revision check with ingestion. The exact planned text is sent; a required rewrite triggers a
new plan. More than five minutes late means replan, not stale delivery. Once a network send
has started it cannot be recalled: incoming events processed after that boundary cannot undo it.
The existing independently committed send intent prevents retries after an uncertain result.
Uncertain delivery requires channel reconciliation, never automatic resend.

The chat displays the proposal and its status. Human messages from admin text, recorded voice,
attachments and the connected account/phone preserve the current AI switch. All use the existing
`operator` message author; admin identity is retained where known. They cancel stale queued AI
text but do not silently resolve handoff or remove an explicit pause. Resume changes current
status and retains the triggering inbound message's historical handoff reason and timestamp.
AI handoff requires a reason in its decision; malformed decisions retry and ultimately follow
the existing explicit failure handoff with a system reason.
The planner also reviews empty-text handoff, stop-contact and refusal decisions. Only a guard
approval with no invented text and an unchanged context can apply those control actions without
queuing a message. A blocked, rewritten or superseded empty decision changes no control state.

Date, weekday, time, offset and timezone are calculated by `persona_clock` from the injected
server UTC clock. Generator and guard receive the same facts; retries/rewrites retain them,
and delivery review refreshes them. The guard's mandatory fact contract also applies when an
older owner-edited guard prompt is stored. Persona style remains outside fact checking.
Calls receive the same computed context. Their audio now waits for a per-turn guard with
fresh facts before playback (see `calls.md`). Natural-language checking remains model-based;
the time source is not.

Migration 0027 adds the revision, JSON plan and message handoff columns. Rollout remains the
existing CI deploy with backup, old worker stop, migration, new code and readiness gates.
Validation is CI-only with synthetic conversations, fake transports/models and disposable
PostgreSQL. No private conversation is included in fixtures. A separately authorized server
audit confirmed that the previous live voice path had no per-utterance guard verdict. Existing
LLM traces retain metadata, not the full historical generation/guard input. The text guard
correction cannot prove what an old voice session received. New playback checks do not
retroactively mark previously unreviewed voice messages as reviewed.
