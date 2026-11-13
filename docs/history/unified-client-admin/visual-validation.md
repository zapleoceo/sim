# Rendered validation — unified client admin

2026-11-07. Independent reviewer: `/root/admin_visual_review` (Codex visual-capable
agent). Branch `codex/unified-client-admin`, base `349dc66`; reviewed uncommitted
feature and final production bundle `main-54DL3F47.js`.

## Verdict

**PASS for the tested local UI scenarios and numeric token/overflow assertions.**
No unresolved major visual regression remains in the exercised screens. This is
not a pixel-parity claim: the source was textual, with no independent Figma/image
reference. Real connectors and production were not used.

Chrome, synthetic local app `http://127.0.0.1:18400`, 1,527 messages and 600 LLM
calls; isolated database, no worker and empty connector registry. The existing
dark theme was preserved. Local operator and superadmin accounts were used;
superadmin was restored after role checks.

## Findings and corrections verified

1. **Major, client workflow, 390px:** 25 full message cards originally preceded
   the existing editing controls. Funnel heading was at y=10019px, client-work
   heading at y=10362.5px. Corrected: funnel y=450px, client work y=793.5px,
   event history y=1491px; conversation follows these sections. Header offers a
   direct conversation button. Actual click keeps `/clients/1` and scrolls the
   conversation heading to approximately y=0.
2. **Major, inbox header, 390px:** new timeline CTA overlapped stage/status badges.
   Corrected to its own row. Name truncation remains conventional; badges, AI
   switch and profile button no longer overlap. `390-inbox-final.png`.
3. **Major, anchor navigation:** initial `href="#conversation"` navigated to
   `/inbox#conversation` because of base href. Corrected button stays on the
   client route. Inbox link resolves `/clients/1#conversation`, with conversation
   visible; direct button aligns it to the top.
4. **Minor, paginator localization:** initial English labels corrected to
   `На странице`, `Следующая страница`, `1–25 из 1527`.
5. **Minor, filter presentation:** channel/persona defaults initially displayed
   empty selected values. Final DOM exposes `Все` for the unselected persona on
   channel deep-link; cleared fields remain identifiable by their labels.

Earlier screenshots without `-final` are diagnostic evidence, not final acceptance.
The browser capture API sometimes returned the preceding frame on the first
screenshot after resize/navigation. Final acceptance images were captured after
priming and the exact same bytes were saved and visually inspected. This was a
capture artifact, not a persistent responsive-sidebar defect.

## Responsive and numeric evidence

All widths below have zero document horizontal overflow for activity and client
screens. LLM log also measured zero overflow across the same matrix. Measured
activity padding: 16px below768 / 24px above; gap16px; heading20px/22px with weight600.
Client cards radius12px/padding16px, title22px/600, conversation heading17px/600.

| Width | Tokens / overflow | Layout |
|---:|---|---|
| 360 | PASS | phone |
| 390 | PASS | phone |
| 767 | PASS | phone boundary |
| 768 | PASS | navigation rail |
| 1023 | PASS | rail boundary |
| 1024 | PASS | rail |
| 1199 | PASS | rail boundary |
| 1200 | PASS | full navigation |
| 1279 | PASS | desktop boundary |
| 1280 | PASS | desktop |
| 1440 | PASS | desktop |
| 1920 | PASS | centered max-width content |

Ran `fidelity-probe.mjs --emit=json` to obtain the spec property plan. The CUA
read-only DOM evaluator collected that plan without injecting styles or clicking
through evaluation (the supported automation boundary). Cross-route probes merge
activity/client measurements at the same width. Then ran `assert-computed.mjs`
for all12 widths and `fidelity-report.mjs`: **132 assertions, 0 failed,
overall.pass=true**. The spec contains only11 numeric assertions per width;
the machine PASS does not assert unencoded scenarios or pixel similarity.

Evidence in `docs/ai-ui/evidence/unified-client-admin/actual/`:

- `probe-<width>.json`, `assert-<width>.json`, `fidelity-report.{json,md}`.
- `<width>-activity-final.png`, `<width>-client-final.png`, `llm-matrix.json`.
- Final user-facing captures: `1440-activity-final.png`,
  `390-inbox-final.png`, `390-client-conversation-final.png`.

## Actual interactions checked

- Message activity loads1527 records; client1 history contains28 records across
  its3 dialogs (two channel types), with correct named operator and model/provider.
  Stored tokens1200/0 and cost0 are visible; missing LLM usage displays dashes.
- ChannelWhatsApp + client1 + authorAI intersection gives exactly1 record;
  reset restores1527. Search with nonexistent text gives0 with an honest empty state.
- Paginator changes1–25 to26–50; refresh resets to1–25. Loading status was observed.
- Mobile filter sheet contains controls and accessible Done/Reset actions.
- Channel card activity link opens `/activity?channel_id=2`, selects the actual
  WhatsApp account and returns267 records.
- Client source link opens `/inbox?d=142`; matching WhatsApp conversation and
  the3 expected messages render. No message was sent.
- Owner LLM tab loads600 calls including failure, missing usage, latency and
  context. No raw provider diagnostic appears in the rendered error record.
- Operator direct `/activity?tab=llm` shows messages and zero LLM tabs; direct
  `/api/activity/llm` renders `Owner role required`.
- Chrome error/warning log was empty during the exercised UI flows; no broken
  rendered image was detected in the measured views.

## Limits

No Figma/pixel diff, production-volume load test, real connector delivery,
Safari/Firefox/Edge execution, exhaustive media/guard states or injected network
failure/retry run. Unit/API tests cover additional state/error behavior separately.
The browser's complete network-response log was not captured; the empty console
and rendered-image checks are narrower evidence. CSS cross-browser behavior was
not independently certified. Numeric checks cover the supplied token subset;
remaining spec states are not all machine-encoded.
