import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { FlaskConical, X } from 'lucide-react';
import { useState } from 'react';
import { StageChip, StatusBadge } from '@/components/badges';
import { EmptyState, ErrorState } from '@/components/empty-state';
import { GuardBadge } from '@/components/guard-badge';
import { Page, PageHeader } from '@/components/page';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { simulationsApi } from '@/lib/api/simulations';
import { formatDateTime, formatTime } from '@/lib/format';
import type { ListResponse, SimBranchRow } from '@/lib/models';
import { qk } from '@/lib/query';
import { cn } from '@/lib/utils';

export const SIM_PAGE_SIZE = 50;

/** An admin without a name comes as «169510539» — show «ID …». */
export function adminLabel(name: string): string {
  return /^\d+$/.test(name.trim()) ? `ID ${name.trim()}` : name;
}

/** Loaded rows without duplicates (a new branch can shift offsets between pages). */
export function mergePages(pages: readonly ListResponse<SimBranchRow>[]): SimBranchRow[] {
  const seen = new Set<number>();
  const out: SimBranchRow[] = [];
  for (const p of pages) {
    for (const b of p.items) {
      if (!seen.has(b.id)) {
        seen.add(b.id);
        out.push(b);
      }
    }
  }
  return out;
}

function Transcript({ id, onClose }: { id: number; onClose: () => void }) {
  const detail = useQuery({ queryKey: qk.simulation(id), queryFn: () => simulationsApi.get(id) });
  if (detail.isError) return <ErrorState onRetry={() => void detail.refetch()} />;
  if (detail.isPending) return <Skeleton className="h-96 rounded-xl" />;
  const d = detail.data;
  const needs = d.needs.join('; ');
  return (
    <section aria-label="Переписка симуляции" className="flex flex-col gap-4 rounded-xl border bg-card p-4 shadow-xs sm:p-5">
      <header className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-base font-semibold">{d.title}</h2>
          <p className="text-sm text-muted-foreground">
            {adminLabel(d.admin.display_name)} · создана {formatDateTime(d.created_at)}
          </p>
        </div>
        <StatusBadge status={d.status} />
        <Button variant="ghost" size="icon-sm" aria-label="Закрыть" onClick={onClose}>
          <X />
        </Button>
      </header>
      {(needs || d.next_step) && (
        <p className="text-sm text-muted-foreground">
          {needs && <>Потребности: {needs}. </>}
          {d.next_step && <>Следующий шаг: {d.next_step}</>}
        </p>
      )}
      <div className="flex flex-col gap-2">
        {d.messages.map((m) => (
          <div
            key={m.id}
            data-role={m.role}
            className={cn(
              'flex max-w-[85%] flex-col gap-1 rounded-2xl px-3 py-2',
              m.role === 'ai' ? 'self-end bg-bubble-ai' : 'self-start bg-bubble-client',
            )}
          >
            <p className="text-sm whitespace-pre-wrap [overflow-wrap:anywhere]">{m.text}</p>
            <span className="text-[11px] text-muted-foreground">
              {m.role === 'client' ? 'клиент' : 'персонаж'} · {formatTime(m.created_at)}
              {m.model ? ` · ${m.model}` : ''}
            </span>
            <GuardBadge guard={m.guard} />
          </div>
        ))}
      </div>
    </section>
  );
}

/** Admins' test chats with personas from the bot (read-only). */
export function SimulationsPage() {
  const [selected, setSelected] = useState<number | null>(null);
  const list = useInfiniteQuery({
    queryKey: qk.simulations,
    queryFn: ({ pageParam }) => simulationsApi.list(null, { limit: SIM_PAGE_SIZE, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((n, p) => n + p.items.length, 0);
      const total = last.total ?? loaded;
      return last.items.length > 0 && loaded < total ? loaded : undefined;
    },
  });

  const rows = mergePages(list.data?.pages ?? []);
  const total = list.data?.pages.at(-1)?.total ?? rows.length;
  const remaining = Math.max(0, total - rows.length);
  const reading = selected !== null;

  return (
    <Page>
      <PageHeader
        title="Симуляции"
        subtitle="Тестовые диалоги админов с персонажами в боте. Не попадают в диалоги, клиентов и аналитику."
      />
      <div className={cn('grid items-start gap-4', reading && 'lg:grid-cols-[minmax(18rem,22rem)_minmax(0,1fr)]')}>
        <div className={cn('flex flex-col gap-2', reading && 'hidden lg:flex')}>
          {list.isError ? (
            <ErrorState onRetry={() => void list.refetch()} />
          ) : list.isPending ? (
            Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-20 rounded-xl" />)
          ) : rows.length === 0 ? (
            <EmptyState icon={FlaskConical} title="Симуляций пока нет" hint="Админ начинает их командой /persona в боте входа." />
          ) : (
            <>
              {rows.map((b) => (
                <button
                  key={b.id}
                  type="button"
                  aria-current={selected === b.id ? 'true' : undefined}
                  onClick={() => setSelected(b.id)}
                  className={cn(
                    'flex flex-col gap-1 rounded-xl border bg-card p-3 text-left shadow-xs transition-colors hover:bg-accent/50',
                    selected === b.id && 'border-primary ring-1 ring-primary',
                  )}
                >
                  <span className="flex items-center justify-between gap-2">
                    <strong className="truncate font-medium">{b.persona.name}</strong>
                    <StageChip stage={b.stage} />
                  </span>
                  <span className="text-sm text-muted-foreground">
                    {adminLabel(b.admin.display_name)} · {b.message_count} сообщ. · {formatDateTime(b.last_active_at)}
                  </span>
                  {b.closed_at && <span className="text-xs text-muted-foreground">закрыта</span>}
                </button>
              ))}
              {remaining > 0 && (
                <Button
                  variant="outline"
                  className="self-center"
                  disabled={list.isFetchingNextPage}
                  onClick={() => void list.fetchNextPage()}
                >
                  {list.isFetchingNextPage ? 'Загрузка…' : `Показать ещё (${remaining})`}
                </Button>
              )}
            </>
          )}
        </div>
        {selected !== null && <Transcript id={selected} onClose={() => setSelected(null)} />}
      </div>
    </Page>
  );
}
