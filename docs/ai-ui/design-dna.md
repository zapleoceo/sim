# SIM Design DNA

Current for the React + shadcn/ui admin. Minimalist, calm neutral surfaces with one indigo accent; content first.

## Tokens

All colours are CSS variables in `frontend/src/index.css` (shadcn convention, OKLCH), switched by the `.dark` class on `<html>`:
`--background`, `--card`, `--muted`, `--accent`, `--primary`, `--destructive`, `--success`, `--warning`, `--border`, `--ring`,
`--sidebar`, message bubbles `--bubble-client|ai|operator`, the seven funnel stages `--stage-1..7` (also `--chart-1..3`).
Use Tailwind utilities bound to these tokens (`bg-card`, `text-muted-foreground`, `text-success`); no hardcoded palette
except channel brand colours in `ChannelIcon`. Radius `--radius` 10px; type Inter Variable, `tabular` utility for numbers.

## Layout

- `Page` container: 16px side padding on phones, 24px from `sm`, max width 72rem (narrow 48rem for editors, wide for the funnel).
- Cards: `rounded-xl border bg-card`, 16–20px padding, light `shadow-xs`. Avoid cards inside cards.
- Navigation: 240px sidebar (64px rail when collapsed, state remembered), phone bottom bar with the first four sections + «Ещё».
- Inbox: list / conversation / optional client card; single pane below 1024px; card from 1280px.

## Patterns

- Lists: TanStack Table (`DataTable`) with server `Pager`, or card grids for small collections (personas, channels).
- Filters: `FilterBar`; changing a filter returns to the first page; debounce text search 250ms.
- Forms: react-hook-form + zod schema per screen, `Field` for labels/errors, primary action right-aligned, destructive action
  as a ghost-destructive button on the left, confirmation through `useConfirm`.
- States: skeletons while loading, `EmptyState` for no data, `ErrorState` with retry for failures — never present a failed
  request as «no data». Background polls are silent; user actions toast once on failure (`lib/errors.ts`).
- Messages: day separators, client left, AI/operator right with author label, delivery icon with tooltip, guard badge;
  text `whitespace-pre-wrap` and `overflow-wrap:anywhere`.

## Accessibility and responsive checks

Focus rings from `--ring`; every icon button has an aria-label; message logs use `role=log` + `aria-live=polite`;
dialogs and menus are Radix (focus trap, Esc). Check 360/390 phones, 767/768, 1023/1024, 1279/1280 and 1440+.
Light, dark and system themes must stay readable.
