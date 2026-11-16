# Inbox clarity

The existing inbox now has one compact current-state block. Priority is AI pause, operator
requirement, delayed reply, then continuation plan. Native details keep the exact plan text,
date/time with browser timezone offset, reasons and existing actions available without
pushing the conversation off a phone screen. Historical handoff marks remain on their
original messages and never imply a new active pause. Technical call issues remain events.

Client, AI and operator messages retain their labels and colors. Internal notes explicitly
say they are not sent. Metadata is collapsed and empty generator diagnostics are omitted;
service events have separate styling and expandable reasons. Message/event timestamps expose
the same full zoned date as scheduled times. All-channel history remains linked on mobile.

Reading history disables automatic scrolling. New messages get a navigation button; service
events do not move the reader. Returning to the bottom resumes message following. Dialog
changes reset the scroll target. Existing permissions, channel capabilities, CSRF, idempotency,
send actions and separate note-editing flows are retained.

Verification is CI only with synthetic component/API fixtures, including state precedence,
long plans, collapsed details, control requests, metadata and mobile navigation. No local
build/tests or live sends are used. Browser visual inspection was unavailable due to an
earlier access restriction; no claim of screenshot/pixel validation or manual assistive
technology verification is made. Independent review and release follow the incident PR.
