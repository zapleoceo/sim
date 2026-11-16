# Guard feedback to the reply agent

When the guard finds a problem in a draft reply or its decision fields, it returns a short reason. The reply agent receives that reason and creates one new complete decision. The guard checks the new draft before it can be queued. If that check also fails, automatic replies pause and the conversation is marked for a manager response.

The guard does not write a replacement reply. For compatibility, a `rewrite` verdict from an older saved guard prompt is treated as an objection; its `fixed_reply` is ignored. A verdict of `ok` with nonempty violations or replacement text is also treated as an objection.

The change applies to normal replies, follow-ups, previews, and isolated simulations. Reengagement regenerates the entire plan, including its proposed time and control flags, at most once. It recalculates trusted facts for each attempt and checks the final candidate at the planned send time. A second objection leaves a blocked plan without sending or adding a pause. Existing revision, signature, opt-out and channel fences remain in force. A corrected no-send decision is valid.

Live voice cannot safely replace already-generated audio with guard text. The current live transport has no feedback/retry command: rejected PCM is discarded and the technical call outcome is recorded without handoff or automatic pause. Existing manual/content restrictions remain. This change does not claim a live voice retry protocol. Trusted time and voice-specific guard rules remain mandatory, including when an older saved prompt requests a rewrite.

Historical `rewritten` records remain readable. CI regressions cover legacy rewrite, contradictory ok, full-decision regeneration, silent control decisions, no-send plans, and the actual mocked call path with no playback or pause. The project requires CI verification; no local tests were run for this change.
