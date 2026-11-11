# Sim admin (React + Vite)

Russian-language admin UI for Sim. Stack, structure, auth contract and test approach:
[docs/admin-frontend.md](../docs/admin-frontend.md). API contract: [docs/api.md](../docs/api.md).

```bash
npm ci
npm run dev          # :4200, /api and /hooks proxied to http://localhost:8000
npm run lint         # ESLint
npm run typecheck    # tsc -b (app, tests, vite config)
npm test             # Vitest + Testing Library (jsdom)
npm run build        # -> dist/sim/browser (served by the backend, packaged by CI)
```

Per `AGENTS.md`, lint/typecheck/tests/build run only in CI on a pushed commit or PR.
Add shadcn/ui components by hand or with `npx shadcn add <name>` (config in `components.json`).
