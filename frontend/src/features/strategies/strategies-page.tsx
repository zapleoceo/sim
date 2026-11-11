import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { ChevronRight, Plus, Route as RouteIcon } from 'lucide-react';
import { EmptyState, ErrorState } from '@/components/empty-state';
import { Page, PageHeader } from '@/components/page';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { strategiesApi } from '@/lib/api/strategies';
import { qk } from '@/lib/query';

/** Strategy list; the editor is a separate page (`/strategies/new`, `/strategies/$id`). */
export function StrategiesPage() {
  const strategies = useQuery({ queryKey: qk.strategies, queryFn: strategiesApi.list });

  return (
    <Page width="narrow">
      <PageHeader title="Стратегии" subtitle="Сценарий общения: цель, оффер, правила и playbook по этапам">
        <Button asChild>
          <Link to="/strategies/new">
            <Plus />
            Новая стратегия
          </Link>
        </Button>
      </PageHeader>

      {strategies.isError ? (
        <ErrorState onRetry={() => void strategies.refetch()} />
      ) : strategies.isPending ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="h-16 rounded-xl" />
          ))}
        </div>
      ) : strategies.data.length === 0 ? (
        <EmptyState icon={RouteIcon} title="Стратегий пока нет" hint="Создайте первую, чтобы привязать её к персонажу." />
      ) : (
        <ul className="flex flex-col divide-y overflow-hidden rounded-xl border bg-card shadow-xs">
          {strategies.data.map((s) => (
            <li key={s.id}>
              <Link
                to="/strategies/$id"
                params={{ id: String(s.id) }}
                className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-accent/50"
              >
                <span className="flex min-w-0 flex-1 flex-col">
                  <strong className="truncate font-medium">{s.name}</strong>
                  <span className="truncate text-sm text-muted-foreground">{s.partner_name || 'Партнёр не указан'}</span>
                </span>
                <ChevronRight className="size-4 flex-none text-muted-foreground" />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Page>
  );
}
