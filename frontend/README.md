# Sim dashboard (React + Vite)

Russian-language dashboard for Sim v2: three sections — Чаты, Персонажи, Лиды.
Structure, settings map and live updates: `docs/frontend.md`. API contract: `docs/api.md`.

```bash
npm ci
npm run dev          # :4200, /api proxied to http://localhost:8000
npm run lint         # ESLint
npm run typecheck    # tsc -b (app, tests, vite config)
npm test             # Vitest + Testing Library (jsdom)
npm run build        # -> dist/sim/browser (served by the backend, packaged by CI)
```

Per `AGENTS.md`, lint/typecheck/tests/build run only in CI on a pushed commit or PR.
