# Explicit unified persona instructions

PR63 now contains only the remaining opt-in unified prompt. Context inspection and
redacted broker payloads already shipped in PR101 and are not duplicated here.

Migration0030 (after0029) adds nullable persona.agent_prompt without updating existing
rows. NULL retains the legacy composed prompt. Only an owner/CSRF-protected
PUT /api/personas/{id}/prompt with nonblank text enables unified mode. Generic persona
updates do not touch this field. The page confirms the first switch, preserves the
exact saved text and refreshes the existing context inspector after saving.

Unified mode replaces description/prohibitions/strategy instruction text, while
mandatory honesty/financial-claim boundaries, link rules, output format, guard,
voice rules and programmatic time/passport/memory remain. The shared builder is used
by reply, preview, simulation, followup, reengagement and calls. Routine still computes
current facts. Saving changes the context signature, invalidating stale call/plan
candidates; the journal stores a field-change marker, not the prompt text.

Tests use fake transports and synthetic text. CI covers migration upgrade/downgrade,
no automatic switch, owner/CSRF/blank limits, generic-save preservation, preview and
voice context, stale signatures and UI confirmation. No production persona is switched
by this PR. No local verification or paid/provider calls; review precedes release.
