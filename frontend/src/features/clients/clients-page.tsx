import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import type { ColumnDef } from '@tanstack/react-table';
import { Search, Users } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Avatar } from '@/components/avatar';
import { DataTable } from '@/components/data-table';
import { EmptyState, ErrorState } from '@/components/empty-state';
import { Page, PageHeader } from '@/components/page';
import { Input } from '@/components/ui/input';
import { useDebounced } from '@/hooks/use-debounced';
import { formatDateTime, formatShort } from '@/lib/format';
import { qk } from '@/lib/query';
import { clientName } from '@/lib/utils';
import type { Client } from '@/lib/types';
import { clientsApi } from './api';
import { ClientAiSwitch } from './client-ai-switch';

export const CLIENT_COLUMNS: ColumnDef<Client, unknown>[] = [
  {
    id: 'name',
    header: 'Имя',
    cell: ({ row }) => {
      const name = clientName(row.original);
      return (
        <Link
          to="/clients/$clientId"
          params={{ clientId: String(row.original.id) }}
          className="flex min-w-0 items-center gap-2.5 font-medium hover:underline"
        >
          <Avatar name={name} size={30} />
          <span className="flex min-w-0 flex-col">
            <span className="truncate">{name}</span>
            {row.original.username && (
              <span className="truncate text-xs font-normal text-muted-foreground md:hidden">@{row.original.username}</span>
            )}
          </span>
        </Link>
      );
    },
    meta: { className: 'w-full max-w-0 md:w-auto md:max-w-none' },
  },
  {
    id: 'username',
    header: 'Username',
    cell: ({ row }) => (row.original.username ? `@${row.original.username}` : '—'),
    meta: { className: 'hidden md:table-cell text-muted-foreground' },
  },
  {
    id: 'phone',
    header: 'Телефон',
    cell: ({ row }) => row.original.phone || '—',
    meta: { className: 'hidden lg:table-cell tabular' },
  },
  {
    id: 'last_seen',
    header: 'Последний контакт',
    cell: ({ row }) => (
      <time dateTime={row.original.last_seen_at ?? undefined} title={formatDateTime(row.original.last_seen_at)}>
        {formatShort(row.original.last_seen_at) || '—'}
      </time>
    ),
    meta: { className: 'hidden sm:table-cell whitespace-nowrap text-muted-foreground tabular' },
  },
  {
    id: 'chats',
    header: 'Чаты',
    cell: ({ row }) => row.original.chats_count ?? '—',
    meta: { className: 'hidden lg:table-cell text-right tabular' },
  },
  {
    id: 'ai',
    header: 'ИИ',
    cell: ({ row }) => (
      <ClientAiSwitch clientId={row.original.id} enabled={row.original.ai_enabled} name={clientName(row.original)} />
    ),
    meta: { className: 'w-16 text-right' },
  },
];

/** «Клиенты»: full-width table with search; the name opens the client card. */
export function ClientsPage() {
  const [search, setSearch] = useState('');
  const q = useDebounced(search.trim(), 300);
  const clients = useQuery({ queryKey: qk.clientList(q), queryFn: () => clientsApi.list(q), placeholderData: (prev) => prev });
  const rows = useMemo(() => clients.data?.items ?? [], [clients.data]);

  return (
    <Page width="wide">
      <PageHeader title="Клиенты" subtitle={clients.data ? `${rows.length} в списке` : undefined}>
        <label className="relative w-full sm:w-72">
          <span className="sr-only">Поиск клиентов</span>
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Имя, username, телефон"
            className="pl-8"
          />
        </label>
      </PageHeader>
      {clients.isError ? (
        <ErrorState onRetry={() => void clients.refetch()} />
      ) : (
        <div className="rounded-xl border bg-card shadow-xs">
          <DataTable
            columns={CLIENT_COLUMNS}
            data={rows}
            loading={clients.isPending}
            getRowId={(c) => String(c.id)}
            caption="Клиенты персонажа"
            empty={
              <EmptyState
                icon={Users}
                title={q ? 'Никого не нашли' : 'Клиентов пока нет'}
                hint={q ? 'Попробуйте другое имя или номер.' : 'Клиент появится, когда напишет персонажу.'}
              />
            }
          />
        </div>
      )}
    </Page>
  );
}
