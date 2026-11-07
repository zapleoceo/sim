# AI UI workflow — generated project knowledge

This folder holds the persistent knowledge and per-task artifacts produced by the
`/implement-ui` workflow. It is generated and maintained by the workflow; edit by
hand only to correct facts.

## What the workflow does
`/implement-ui` implements or aligns Web UI from a prototype (Figma, screenshots,
HTML prototype, staging page, or textual spec) with maximum visual accuracy while
preserving this project's architecture, business logic, conventions, component
library, and design system. It runs discovery → design-DNA → prototype analysis →
component reuse → plan → implementation → functional + visual validation →
iterative correction → quality review → report.

## Invocation
```
/implement-ui <task text: issue id, route, component, prototype link, screenshots, notes>
```
Examples:
- `/implement-ui LOG-6087 Implement the class-work page from the attached prototype.`
- `/implement-ui Update /reports/students to match the desktop and mobile screenshots. Preserve all filters and export behavior.`
- `/implement-ui Implement the selected Figma frame for the diploma module. Validate at 1440×900 and 390×844.`

You only ever invoke `/implement-ui`. The supporting stages run automatically.

## Generated documents
- `project-profile.md` — stack, scripts, structure (Phase 1).
- `design-dna.md` — the project's actual UI system (Phase 2).
- `component-catalog.md`, `page-patterns.md` — reusable building blocks (grow over time).
- `decisions.md` — durable architectural/UI decisions log.
- `tasks/<slug>/` — task-context, prototype-spec, component-reuse, implementation-plan, final-report.
- `evidence/<slug>/` — rendered screenshots, diffs, overlays (git-ignored).

## Refreshing stale knowledge
`project-profile.md` and `design-dna.md` carry a lightweight fingerprint (paths +
mtime/size of manifests and theme entry points). The workflow refreshes them when
those change, when a document marks itself stale, or when you ask for a refresh.

## Visual evidence
Stored under `evidence/<slug>/{reference,actual,diff,overlay,reports}`. These are
git-ignored by default; delete freely.

## Optional tools (enhance, not required)
- A connected **browser MCP** or **Playwright** for screenshots.
- **pixelmatch/pngjs** (or similar) for image diffs.
- A connected **Figma MCP** for exact design metadata.
When absent, the workflow falls back to structural validation and says so.

## Troubleshooting browser validation
- Dev server not up → the workflow waits via `wait-for-url.mjs`; check the port
  in `project-profile.md`.
- Playwright missing → install it in the project or connect a browser MCP; the
  workflow will not install heavy deps silently.
- Auth-protected route → provide the project's dev/test auth path.

## Customizing / updating the workflow
The workflow itself lives in `~/.claude/skills/implement-ui/` (orchestrator,
references, templates, scripts) and `~/.claude/agents/ui-*`. Safe to customize:
the reference rule files and templates. The phase list and gates are the contract
— change them deliberately.