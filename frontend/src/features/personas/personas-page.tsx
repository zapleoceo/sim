import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { Plus, Smile } from 'lucide-react';
import { Avatar } from '@/components/avatar';
import { EmptyState, ErrorState } from '@/components/empty-state';
import { Page, PageHeader } from '@/components/page';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { personasApi } from '@/lib/api/personas';
import { qk } from '@/lib/query';

export function PersonasPage() {
  const personas = useQuery({ queryKey: qk.personas, queryFn: personasApi.list });

  return (
    <Page>
      <PageHeader title="Персонажи" subtitle="Виртуальные представители, которые ведут диалоги">
        <Button asChild>
          <Link to="/personas/new">
            <Plus />
            Новый персонаж
          </Link>
        </Button>
      </PageHeader>

      {personas.isError ? (
        <ErrorState onRetry={() => void personas.refetch()} />
      ) : personas.isPending ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,20rem),1fr))] gap-3">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="h-24 rounded-xl" />
          ))}
        </div>
      ) : personas.data.length === 0 ? (
        <EmptyState icon={Smile} title="Персонажей пока нет" hint="Создайте первого — он будет вести диалоги в каналах." />
      ) : (
        <ul className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,20rem),1fr))] gap-3">
          {personas.data.map((p) => (
            <li key={p.id}>
              <Link
                to="/personas/$id"
                params={{ id: String(p.id) }}
                className="flex h-full items-center gap-3 rounded-xl border bg-card p-4 shadow-xs transition-colors hover:bg-accent/50"
              >
                <Avatar name={p.name} url={p.avatar_url} size={52} />
                <span className="flex min-w-0 flex-col gap-1">
                  <strong className="truncate font-medium">{p.name}</strong>
                  <span className="flex min-w-0 items-center gap-2 text-xs">
                    <Badge variant={p.is_active ? 'success' : 'muted'}>{p.is_active ? 'Активен' : 'Выключен'}</Badge>
                  </span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Page>
  );
}
