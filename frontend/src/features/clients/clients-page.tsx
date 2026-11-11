import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate } from '@tanstack/react-router';
import type { ColumnDef } from '@tanstack/react-table';
import { Users } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Avatar } from '@/components/avatar';
import { ChannelIcon, StageChip, StatusBadge } from '@/components/badges';
import { DataTable } from '@/components/data-table';
import { EmptyState, ErrorState } from '@/components/empty-state';
import { FilterBar, FilterField, SearchInput } from '@/components/filter-bar';
import { OptionSelect } from '@/components/option-select';
import { Page, PageHeader } from '@/components/page';
import { Pager } from '@/components/pager';
import { Card } from '@/components/ui/card';
import { useDebounced } from '@/hooks/use-debounced';
import { clientsApi } from '@/lib/api/clients';
import type { PageRequest } from '@/lib/api/http';
import { personasApi } from '@/lib/api/personas';
import { formatShort } from '@/lib/format';
import type { Client, ClientFilter, ClientStatus, Stage } from '@/lib/models';
import { qk } from '@/lib/query';
import { STAGES, STATUSES, STATUS_LABELS } from '@/lib/stages';
import { DEFAULT_CLIENT_FILTER, clientApiFilter, clientFilterCount } from './client-logic';

const columns: ColumnDef<Client, unknown>[] = [
  {
    id: 'client',
    header: 'Клиент',
    cell: ({ row: { original: c } }) => (
      <span className="flex min-w-0 items-center gap-3">
        <Avatar name={c.display_name} url={c.avatar_url} size={36} />
        <span className="flex min-w-0 flex-col">
          <Link to="/clients/$id" params={{ id: String(c.id) }} className="truncate font-medium hover:underline">
            {c.display_name}
          </Link>
          <span className="truncate text-xs text-muted-foreground">{c.next_step || 'Следующий шаг не задан'}</span>
        </span>
      </span>
    ),
    meta: { className: 'max-w-[16rem] sm:max-w-none' },
  },
  {
    id: 'stage',
    header: 'Этап',
    cell: ({ row: { original: c } }) => (
      <span className="flex flex-wrap gap-1.5">
        <StageChip stage={c.stage} />
        <span className="sm:hidden">
          <StatusBadge status={c.status} />
        </span>
      </span>
    ),
  },
  {
    id: 'status',
    header: 'Статус',
    cell: ({ row: { original: c } }) => <StatusBadge status={c.status} />,
    meta: { className: 'hidden sm:table-cell' },
  },
  {
    id: 'channels',
    header: 'Каналы',
    cell: ({ row: { original: c } }) => (
      <span className="flex gap-1">
        {c.channels.map((ch) => (
          <ChannelIcon key={ch.dialog_id} kind={ch.kind} size={20} />
        ))}
      </span>
    ),
    meta: { className: 'hidden md:table-cell' },
  },
  {
    id: 'last',
    header: 'Контакт',
    cell: ({ row: { original: c } }) => (
      <span className="text-xs text-muted-foreground tabular">{formatShort(c.last_contact_at)}</span>
    ),
    meta: { className: 'hidden text-right sm:table-cell' },
  },
];

export function ClientsPage() {
  const navigate = useNavigate();
  const [filter, setFilterState] = useState<ClientFilter>(DEFAULT_CLIENT_FILTER);
  const [page, setPage] = useState<PageRequest>({ limit: 50, offset: 0 });
  const q = useDebounced(filter.q, 250);
  const apiFilter = useMemo(() => clientApiFilter({ ...filter, q }), [filter, q]);

  // Any filter change goes back to the first page.
  const setFilter = (next: ClientFilter): void => {
    setFilterState(next);
    setPage((p) => ({ ...p, offset: 0 }));
  };
  const set = <K extends keyof ClientFilter>(key: K, value: ClientFilter[K]): void => setFilter({ ...filter, [key]: value });

  const personas = useQuery({ queryKey: qk.personas, queryFn: personasApi.list });
  const list = useQuery({
    queryKey: qk.clients(apiFilter, page),
    queryFn: () => clientsApi.list(apiFilter, page),
    placeholderData: (prev) => prev,
  });
  const total = list.data?.total ?? list.data?.items.length ?? 0;
  const count = clientFilterCount(filter);

  return (
    <Page>
      <PageHeader title="Клиенты" subtitle={`${total} в выборке`} />
      <FilterBar
        activeCount={count}
        onReset={() => setFilter(DEFAULT_CLIENT_FILTER)}
        search={<SearchInput value={filter.q} onChange={(v) => set('q', v)} placeholder="Поиск по имени" />}
        filters={
          <>
            <FilterField label="Этап">
              <OptionSelect<Stage | ''>
                value={filter.stage}
                onChange={(v) => set('stage', v)}
                options={[{ value: '', label: 'Все этапы' }, ...STAGES.map((s) => ({ value: s.id, label: s.label }))]}
              />
            </FilterField>
            <FilterField label="Статус">
              <OptionSelect<ClientStatus | ''>
                value={filter.status}
                onChange={(v) => set('status', v)}
                options={[{ value: '', label: 'Любой' }, ...STATUSES.map((s) => ({ value: s, label: STATUS_LABELS[s] }))]}
              />
            </FilterField>
            <FilterField label="Персонаж">
              <OptionSelect<number | null>
                value={filter.persona_id}
                onChange={(v) => set('persona_id', v)}
                options={[{ value: null, label: 'Все' }, ...(personas.data ?? []).map((p) => ({ value: p.id, label: p.name }))]}
              />
            </FilterField>
          </>
        }
      />
      <Card className="gap-0 overflow-hidden p-0 sm:p-0">
        {list.isError && !list.data ? (
          <ErrorState onRetry={() => void list.refetch()} />
        ) : (
          <DataTable
            caption="Клиенты"
            columns={columns}
            data={list.data?.items ?? []}
            loading={list.isPending}
            getRowId={(c) => String(c.id)}
            onRowClick={(c) => void navigate({ to: '/clients/$id', params: { id: String(c.id) } })}
            empty={<EmptyState icon={Users} title="Клиентов не найдено" hint="Измените фильтры или дождитесь первых диалогов." />}
          />
        )}
      </Card>
      {total > 25 && <Pager page={page} total={total} onChange={setPage} label="Страницы списка клиентов" />}
    </Page>
  );
}
