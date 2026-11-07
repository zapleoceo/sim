# SIM Design DNA

Source-verified 2026-11-07, base `349dc66`. Labels distinguish actual rules from recommendations. No pixel reference was supplied; existing components + textual task are the presentation baseline.

## Theme and tokens

**CONFIRMED** M3 theme is `frontend/src/styles.scss:1–14`: azure primary, blue tertiary, Inter, density0, system light/dark preference. Use Material theme variables and existing `--sim-*` tokens; do not introduce a parallel palette.

| Category | Actual value / source |
|---|---|
| Spacing | `--sim-space-1..7`: 4,8,12,16,24,32,48px (`styles.scss:17`) |
| Radius | `--sim-radius:12px`, `--sim-radius-sm:8px` (`styles.scss:24`) |
| Navigation | full232px, rail72px, bottom56px + safe-area (`styles.scss:26`, `shell.component.scss`) |
| Surfaces | bg/surface/surface-2/border use `light-dark()` pairs (`styles.scss:31`) |
| Text | `--sim-text`, `--sim-text-muted`; body14px/1.5; headings22/17/15px, weight600 (`styles.scss:62`) |
| Accent/status | `--sim-accent=--mat-sys-primary`, accent-soft, ok/warn/danger; muted seven-stage palette (`styles.scss:37`) |
| Chat | client neutral, AI blue, operator green via `--sim-bubble-*` (`styles.scss:42`) |
| Density | `.sim-dense` Material -1; phone returns0 (`styles.scss:124`) |

**CONFIRMED:** fonts load from Google Fonts (`src/index.html:12–15`); screenshot QA must verify fonts actually loaded. Numeric helpers use tabular figures (`.sim-num`). **LOCAL EXCEPTION:** existing bubble/card small badges use some literal 2/4/6px gaps and radii; prefer tokenized new shared components rather than spreading those literals. No global elevation scale exists; bordered flat cards dominate.

**LOCAL EXCEPTION, conversation metadata:** existing inbox metadata/footer uses 12px text.
The extracted shared message metadata preserves this size and tabular numeric helpers;
it is not a new application-wide typography scale. Media keeps the existing 280px cap.

## Layout and interaction patterns

- **DOMINANT:** `.sim-page` column, 24px padding, 16px gap, max1280 centered; phone16px; narrow max760 and wide unlimited variants (`styles.scss:90,140`). `.sim-card` white/dark surface, border, radius12, padding16. Client-card is a local max880 exception (`features/clients/client-card.component.scss:1`).
- **CONFIRMED:** Material sidenav/list + bottom navigation; active text/icon accent, soft active indicator, muted inactive items, hover surface2, safe-area bottom padding (`shell/shell.component.scss`). First four accessible navigation entries appear in bottom bar (`shell/shell.component.ts:14`). New activity navigation must remain reachable via drawer even if not bottom-listed.
- **DOMINANT:** `PageHeaderComponent` provides title/subtitle/action wrapping; phone actions expand beneath heading (`shared/page-header.component.ts`). Use it on activity screen.
- **CONFIRMED:** `FilterBarComponent` projects search and a single `simFilters` template. Desktop wraps compact fields; phone uses Material bottom sheet with reset/done, reused form controls and active-count badge. `alwaysSheet` supports narrow inbox columns; `stackOnPhone` supports long search/date controls (`shared/filter-bar.component.ts:45`). Fields use outline + dynamic subscript, 180px select fields, growing search (`styles.scss:130`, `clients.component.ts:42`).
- **DOMINANT:** typed reactive FormGroup/FormControl, Material input/select/datepicker. `DatetimeFieldComponent` is reusable date/time control, not a freeform string field. Errors through mat-error and centralized toast; explicit labels and icon aria-labels (`shared/datetime-field.component.ts`, `features/channels/channel-wizard.component.html`, `core/interceptors.ts`).
- **CONFIRMED:** clients use semantic clickable list rows and Material paginator, not a fake Material table (`features/clients/clients.component.ts:79,108`). Page sizes and totals are server-derived; filter changes reset page index. Activity may use a table/list but should preserve responsive readability rather than force all metadata columns on phone.
- **CONFIRMED:** inbox is full-height list/conversation, 340px list and optional300px client sidebar; single-pane under1024. Conversation has day separators, author colors, text pre-wrap/overflow-wrap, media/audio and status footers. Blocked drafts have dashed danger border (`features/inbox/inbox.component.scss:1,50`; template `:156`). Preserve draft/delivery distinction; tokens/model metadata must not imply actual delivery.
- **CONFIRMED:** existing client event history is an ordered icon/text list (funnel + partner events), distinct from message conversation (`features/clients/client-card.component.html:89`). Do not replace or silently merge its business controls.
- **CONFIRMED:** Material dialogs/confirmation service and connector wizard are current modal pattern; wizard is520px desktop with viewport-constrained sizing (`channels.component.ts`, `shared/confirm-dialog.component.ts`). No established client timeline tab pattern was found; any tabs should be Material or simple accessible navigation consistent with this theme.
- **CONFIRMED:** `EmptyStateComponent` provides icon/title/hint/projected action, max480 centered; use explicit loading/error separately so a failed request is not presented as no messages (`shared/empty-state.component.ts`). Some existing pages swallow errors with EMPTY and retain loading/old data; this is an inconsistency, not a pattern to copy.
- **CONFIRMED:** `ToastService` info3500ms/error6000ms, centered bottom with close action; danger snackbar global class (`core/toast.service.ts`, `styles.scss:113`). Do not show raw provider errors/secrets in activity UI.

## Accessibility, states and responsive checks

**CONFIRMED:** global focus-visible accent outline2px; icon buttons44px phone; reduced-motion override; safe-area support. Inbox rows use selected/hover state, media text wraps, message container uses `role=log`, polite aria-live (`styles.scss:77,160,165`, inbox template). Buttons use disabled and Material hover/focus behavior. CDK drag animations200ms are funnel-specific, not a reason to animate message updates.

**Recommended:** preserve focus on refresh, label every metadata/filter control, expose loading and retry, distinguish null data from zero, show full timestamp on demand, avoid live-region re-announcing the entire paged history, and keep content readable in both color schemes. Verify 360/390 mobile plus 767/768,1023/1024,1199/1200,1279/1280 boundaries and desktop1440/1920 (from `shared/breakpoints.ts`).

## Allowed / discouraged

- Reuse existing Material components, `--sim-*` tokens, shared filter/header/avatar/channel/status/guard components and formatting functions.
- Keep feature SCSS scoped. Existing Material customization uses system/component CSS variables and `mat.theme`, not new deep MDC descendant hacks.
- Do not add Bootstrap/Tailwind, a new icon font, hardcoded palette, duplicated filter sheet or untyped API DTOs.
- Avoid whole-page horizontal overflow for metadata. Long message/model/channel identifiers need wrapping or explicit accessible truncation.
- No `::ng-deep` convention was found in sampled task surfaces. Global `!important` is confined to reduced-motion policy; do not add feature overrides.

## Open questions

Minimum supported viewport is unspecified (360 used as a QA assumption). Existing history lacks some requested metadata, so historical blanks must remain explicit until backend provenance exists. Exact UI layout is a textual design interpretation, not pixel matching to an external Figma reference.
