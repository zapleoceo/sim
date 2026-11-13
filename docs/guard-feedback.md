# Guard feedback to the reply agent

When the guard finds a problem in a draft reply or its decision fields, it returns a short reason. The reply agent receives that reason and creates one new complete decision. The guard checks the new draft before it can be queued. If that check also fails, automatic replies pause and the conversation is marked for a manager response.

The guard does not write a replacement reply. For compatibility, a `rewrite` verdict from an older saved guard prompt is treated as an objection; its `fixed_reply` is ignored. A verdict of `ok` with nonempty violations or replacement text is also treated as an objection.

The change applies to normal replies, follow-ups, previews, and isolated simulations. Historical `rewritten` records remain readable. The project requires CI verification; no local tests were run for this change.
