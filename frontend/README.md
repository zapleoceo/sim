# Sim admin panel (Angular 19)

Russian-language admin UI for the Sim platform. Contract: `docs/api.md`.

```bash
npm ci
npm start                 # dev server on :4200, /api and /hooks proxied to http://localhost:8000
npm run build             # production build -> dist/sim/browser (served by the backend)
npx ng test --watch=false --browsers=ChromeHeadless
npx ng lint
```

Layout: `src/app/core` (typed API services, auth, interceptors), `shared` (UI building blocks),
`shell` (responsive nav), `features/*` (lazy routes: login, inbox, clients, funnel, dashboard,
personas, strategies, channels, content, partner). Charts are pure CSS/SVG (no chart library).
