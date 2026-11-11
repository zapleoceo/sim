import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useParams } from '@tanstack/react-router';
import { Route as RouteIcon } from 'lucide-react';
import { toast } from 'sonner';
import { useConfirm } from '@/components/confirm';
import { EmptyState, ErrorState } from '@/components/empty-state';
import { BackLink, Page, PageHeader } from '@/components/page';
import { Skeleton } from '@/components/ui/skeleton';
import { strategiesApi } from '@/lib/api/strategies';
import type { Strategy, StrategyIn } from '@/lib/models';
import { qk } from '@/lib/query';
import { positiveInt, upsertById } from '@/lib/utils';
import { StrategyEditor } from './strategy-editor';

/** Strategy editor page: `/strategies/new` and `/strategies/$id`. */
export function StrategyPage() {
  const params: { id?: string } = useParams({ strict: false });
  const isNew = params.id === undefined;
  const id = positiveInt(params.id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const confirm = useConfirm();

  const strategies = useQuery({ queryKey: qk.strategies, queryFn: strategiesApi.list });
  const strategy = id === null ? null : (strategies.data?.find((s) => s.id === id) ?? null);
  const missing = !isNew && strategies.isSuccess && strategy === null;

  const save = useMutation({
    mutationFn: (body: StrategyIn) => (strategy ? strategiesApi.update(strategy.id, body) : strategiesApi.create(body)),
    onSuccess: (saved: Strategy) => {
      queryClient.setQueryData<Strategy[]>(qk.strategies, (list) => upsertById(list ?? [], saved));
      toast.success('Стратегия сохранена');
      if (strategy?.id !== saved.id) {
        void navigate({ to: '/strategies/$id', params: { id: String(saved.id) }, replace: true });
      }
    },
  });

  const remove = useMutation({
    mutationFn: (sid: number) => strategiesApi.remove(sid),
    onSuccess: (_, sid) => {
      queryClient.setQueryData<Strategy[]>(qk.strategies, (list) => (list ?? []).filter((s) => s.id !== sid));
      void navigate({ to: '/strategies' });
    },
  });

  const askRemove = async (s: Strategy): Promise<void> => {
    const ok = await confirm({ title: 'Удалить стратегию?', message: `«${s.name}» будет удалена.`, confirmLabel: 'Удалить', danger: true });
    if (ok) remove.mutate(s.id);
  };

  const title = strategy ? strategy.name : missing ? 'Стратегия' : 'Новая стратегия';

  return (
    <Page width="narrow">
      <BackLink to="/strategies">Все стратегии</BackLink>
      {strategies.isError ? (
        <ErrorState onRetry={() => void strategies.refetch()} />
      ) : strategies.isPending ? (
        <div className="flex flex-col gap-4" aria-busy="true">
          <Skeleton className="h-8 w-56" />
          <Skeleton className="h-[28rem] rounded-xl" />
        </div>
      ) : (
        <>
          <PageHeader
            title={title}
            subtitle={strategy ? 'Редактирование' : missing ? undefined : 'Цель, оффер, правила и playbook по этапам'}
          />
          {missing ? (
            <EmptyState icon={RouteIcon} title="Стратегия не найдена" hint="Возможно, она была удалена." />
          ) : (
            <StrategyEditor
              key={strategy ? `${strategy.id}:${strategy.created_at}` : 'new'}
              strategy={strategy}
              saving={save.isPending}
              onSave={(body) => save.mutate(body)}
              onRemove={strategy ? () => void askRemove(strategy) : undefined}
            />
          )}
        </>
      )}
    </Page>
  );
}
