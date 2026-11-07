# SIM UI project profile

Verified 2026-11-07 on base `349dc6648fb6808ff18c982260b40d138e3244e6`, task `unified-client-admin`.
Evidence paths are repository-relative. This is source discovery, not rendered acceptance.

## Stack and boundaries

- **CONFIRMED:** one Angular **19** application in `frontend/`; Angular CLI application builder, npm + `package-lock.json`, TypeScript 5.7; no Nx/workspace library boundary in `angular.json`. Manifest: `frontend/package.json:1`; builder/entry: `frontend/angular.json:18`.
- **CONFIRMED:** Angular Material/CDK 19, M3 `mat.theme`, SCSS; Inter + Material Icons Outlined webfonts (`frontend/src/styles.scss:1`, `frontend/src/index.html:12`). Charts use ng2-charts 8/Chart.js 4.5.1 (`frontend/package.json`, `features/dashboard/charts.component.ts`). No `@front/*` dependency.
- **CONFIRMED:** `core/` owns typed DTOs, HTTP clients, auth, guards and interceptors; `features/` owns routed screens; `shared/` owns presentation helpers/components; `shell/` owns navigation. `app.routes.ts` lazy-loads components; component input binding supplies route/query parameters (`app.config.ts:15`).
- **DOMINANT:** 32 source files with `@Component` and 32 with OnPush; no NgModules, FormBuilder, ngModel, *ngIf or *ngFor found. Built-in control flow, `inject`, typed reactive forms and signals + RxJS coexist. Cleanup uses `takeUntilDestroyed` in 18 files. Representative: `features/clients/clients.component.ts`, `features/inbox/inbox.component.ts`.
- **CONFIRMED:** strict TS and strict templates enabled; relative imports, no path aliases (`frontend/tsconfig.json:7`). ESLint enforces standalone/OnPush/no-explicit-any plus template accessibility (`frontend/eslint.config.js:8`).
- **CONFIRMED:** Russian literal labels, `LOCALE_ID=ru`, native date locale `ru-RU`; no translation library in manifest (`app.config.ts:19`). Auth/CSRF interceptors centralize request preparation and errors (`core/interceptors.ts`); roles have matching UI guards and navigation access (`core/guards.ts`, `shell/nav.ts`).

## Existing task surfaces

- **CONFIRMED:** `/inbox` is dialog-oriented, with list, selected conversation and optional client sidebar; current detail returns one dialog's messages. Its `role=log` renders media, guard/delivery status and author label (`features/inbox/inbox.component.html:156`).
- **CONFIRMED:** `/clients/:id` shows metadata, linked channel/dialog links, funnel controls and funnel/partner event history; it does not yet consolidate message bodies (`features/clients/client-card.component.html:13,89`, `client-card.component.ts:28`).
- **CONFIRMED:** baseline routes/nav have no activity-log route (`app.routes.ts`, `shell/nav.ts:6`). Existing `Message` DTO has optional model but no token fields/operator identity (`core/models.ts:116`); backend contract expansion is required.
- **CONFIRMED:** channels screen and wizard already implement persona selection, Telegram token/user login, WhatsApp QR and Instagram verification states (`features/channels/channels.component.ts`, `channel-wizard.component.ts:25`). Preserve these rather than replacing with disconnected mock modules.

## Responsive strategy

**CONFIRMED: responsive**, including phone. Viewport meta is device-width (`src/index.html:7`); CDK queries and SCSS explicitly change navigation and pane layout (`shared/breakpoints.ts:7–16`, `shell/shell.component.ts`, `features/inbox/inbox.component.scss`).

| Boundary | Existing behavior |
|---|---|
| 767 / 768 | Phone uses bottom navigation/drawer, filter bottom-sheet, 16px page padding and touch-sized controls. Tablet enters icon rail. |
| 1023 / 1024 | Inbox changes from one pane to list + conversation. |
| 1199 / 1200 | Navigation rail changes to full sidebar. |
| 1279 / 1280 | Inbox can show client detail as third pane. |

**UNKNOWN:** minimum supported width is not formally declared. **INFERRED validation minimum:** 360 CSS px; include 390 phone and 1920 desktop, not a new application breakpoint. For task fidelity thresholds use `requireMobile=true`, mobileWidths `[360,390,767]`, matrixWidths `[360,390,767,768,1023,1024,1199,1200,1279,1280,1440,1920]`, boundaryPairs `[[767,768],[1023,1024],[1199,1200],[1279,1280]]`. This recommendation is derived from source; no layout screenshots were captured in discovery.

## Commands and validation

From `frontend/`: `npm ci`; `npm start` (4200, proxy `/api` and `/hooks` to localhost:8000 in `proxy.conf.json`); `npm run build`; `npm exec -- ng lint`; `npm test -- --watch=false --browsers=ChromeHeadless`; `npm exec -- tsc -p tsconfig.spec.json --noEmit`.

**CONFIRMED:** Karma/Jasmine runner and Chrome launcher, not Vitest; no project E2E/Playwright/Cypress config found. Unit specs and routed smoke specs exist. Build budgets: initial warning1MB/error2MB, component styles warning8kB/error16kB (`angular.json`). No format script declared.

**Recommended runtime QA:** isolated local backend + synthetic test/demo records, never production identity mutation or channel calls; render new/changed routes at representative widths and all boundary pairs, light and dark; exercise loading/error/empty/data and filtering/paging/navigation. Check console, keyboard focus, long text/model names, null metadata and overflow. Actual screenshot review remains a later gate.

Generated/do-not-edit: `node_modules`, `.angular`, `dist`, coverage output. `package-lock.json` is npm-managed; avoid manual edits. Source components can use external files or inline templates/styles; preserve local pattern.

## Lightweight fingerprint

SHA256 prefixes at discovery (refresh when changed):

| Path | Bytes | SHA256 prefix |
|---|---:|---|
| frontend/package.json |1297|6a73e50e28df|
| frontend/package-lock.json |632496|93c2ad360615|
| frontend/angular.json |3243|acd054b3425c|
| frontend/tsconfig.json |942|4f90851feaf3|
| frontend/src/styles.scss |6233|cde0ce3ccd09|
