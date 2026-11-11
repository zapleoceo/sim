# Frontend project profile

Current since the React rebuild (replaces the Angular 19 + Material profile). Details: [../admin-frontend.md](../admin-frontend.md).

## Stack

- One React 19 + TypeScript (strict, `verbatimModuleSyntax`) SPA in `frontend/`, built by Vite 7; npm + `package-lock.json`; Node 22 (`engines`).
- UI: shadcn/ui components in `src/components/ui` (Radix UI primitives + Tailwind CSS 4, tokens in `src/index.css`), lucide-react icons, Inter Variable self-hosted via `@fontsource-variable/inter`.
- Data: TanStack Query (query keys in `src/lib/query.ts`), typed API objects in `src/lib/api/*` over one fetch client (`src/lib/api/http.ts`).
- Routing: TanStack Router, code-based route tree in `src/router.tsx`; every page is a lazy chunk; search params validated in `src/lib/search.ts`.
- Forms: react-hook-form + zod schemas (`features/*/*-schema.ts`); tables: TanStack Table via `components/data-table.tsx`; charts: Recharts (dashboard only); drag and drop: dnd-kit (funnel).

## Product surfaces

Routes and roles are unchanged from the Angular admin: `/login`, `/studio`, `/inbox?d=`, `/clients`, `/clients/$id`,
`/activity?tab=llm&channel_id=`, `/funnel`, `/dashboard`, `/personas[/new|/$id]`, `/settings` (Промпты), `/strategies[/new|/$id]`, `/simulations`,
`/channels`, `/content`, `/partner`, `/admins`. Owner pages are guarded in `beforeLoad`; `/admins` is superadmin only.

## Responsive

Phone first from 360px: bottom tab bar + «Ещё» sheet below 768px, collapsible sidebar above; inbox single pane below 1024px and
client side card from 1280px (`src/hooks/use-media-query.ts`). Filters open in a bottom sheet on phones.

## Tests and CI

Vitest + Testing Library in jsdom (`npm test`); page tests render the whole app on a memory router with a fake backend
(`src/test/render-app.tsx`, `src/test/fake-api.ts`). ESLint flat config, `tsc -b`. Everything runs only in CI (`AGENTS.md`).
Generated/do-not-edit: `node_modules`, `dist`, `coverage`; `package-lock.json` is npm-managed.
