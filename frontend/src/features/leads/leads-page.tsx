import { useQuery } from '@tanstack/react-query';
import { Link, getRouteApi } from '@tanstack/react-router';
import type { ColumnDef } from '@tanstack/react-table';
import { Users } from 'lucide-react';
import { useMemo } from 'react';
import { Avatar } from '@/components/avatar';
import { DataTable } from '@/components/data-table';
import { EmptyState, ErrorState } from '@/components/empty-state';
import { Page, PageHeader } from '@/components/page';
import { SearchInput } from '@/components/search-input';
import { Badge } from '@/components/ui/badge';
import { NativeSelect } from '@/components/ui/native-select';
import { PersonaFilter } from '@/features/persona/persona-filter';
import { usePersonaList } from '@/features/persona/use-persona-list';
import { formatDateTime, formatShort } from '@/lib/format';
import { qk } from '@/lib/query';
import type { Lead } from '@/lib/types';
import { clientName } from '@/lib/utils';
import type { LeadsSearch } from '@/router';
import { leadsApi } from './api';
import { ClientAiSwitch } from './client-ai-switch';
import { STAGES, isStage, stageInfo } from './passport-logic';

const route = getRouteApi('/_app/leads');

export const LEAD_COLUMNS: ColumnDef<Lead, unknown>[] = [
  {
    id: 'name',
    header: 'Имя',
    cell: ({ row }) => {
      const name = clientName(row.original);
      return (
        <Link
          to="/leads/$clientId"
          params={{ clientId: String(row.original.id) }}
          className="flex min-w-0 items-center gap-2.5 font-medium hover:underline"
        >
          <Avatar name={name} size={30} />
          <span className="flex min-w-0 flex-col">
            <span className="truncate">{name}</span>
            {row.original.username && (
              <span className="truncate text-xs font-normal text-muted-foreground">@{row.original.username}</span>
            )}
          </span>
        </Link>
      );
    },
    meta: { className: 'w-full max-w-0 md:w-auto md:max-w-none' },
  },
  {
    id: 'persona',
    header: 'Персонаж',
    cell: ({ row }) => row.original.persona?.name ?? '—',
    meta: { className: 'hidden lg:table-cell' },
  },
  {
    id: 'stage',
    header: 'Стадия',
    cell: ({ row }) => {
      const stage = stageInfo(row.original.passport.stage);
      return <Badge variant={stage.variant}>{stage.label}</Badge>;
    },
    meta: { className: 'whitespace-nowrap' },
  },
  {
    id: 'temperature',
    header: 'Теплота',
    cell: ({ row }) => row.original.passport.temperature,
    meta: { className: 'hidden md:table-cell text-right tabular' },
  },
  {
    id: 'city',
    header: 'Город',
    cell: ({ row }) => row.original.passport.city || '—',
    meta: { className: 'hidden xl:table-cell' },
  },
  {
    id: 'age',
    header: 'Возраст',
    cell: ({ row }) => row.original.passport.age ?? '—',
    meta: { className: 'hidden xl:table-cell text-right tabular' },
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
    id: 'messages',
    header: 'Сообщений',
    cell: ({ row }) => row.original.messages_count,
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

/**
 * «Лиды»: every client of every persona, filters by persona and stage, search; a click opens the
 * card. The filters live in the address (`/leads?q=анна&persona=2&stage=hot`): «назад» from a card
 * and a shared link keep them; «Персонажи» links here with `?persona=`.
 */
export function LeadsPage() {
  const { q = '', persona: personaId = 0, stage = '' } = route.useSearch();
  const navigate = route.useNavigate();
  const setFilters = (next: LeadsSearch): void => void navigate({ search: (prev) => ({ ...prev, ...next }), replace: true });
  const filters = { q, personaId, stage };
  const { rows: personas } = usePersonaList();
  const leads = useQuery({
    queryKey: qk.clientList(q, personaId, stage),
    queryFn: () => leadsApi.list(filters),
    placeholderData: (prev) => prev,
  });
  const rows = useMemo(() => leads.data?.items ?? [], [leads.data]);
  const filtered = Boolean(q || personaId || stage);

  return (
    <Page width="wide">
      <PageHeader title="Лиды" subtitle={leads.data ? `${rows.length} в списке` : undefined}>
        <div className="w-[calc(50%-0.25rem)] sm:w-48">
          <PersonaFilter value={personaId} onChange={(id) => setFilters({ persona: id || undefined })} personas={personas} />
        </div>
        <div className="w-[calc(50%-0.25rem)] sm:w-40">
          <NativeSelect
            aria-label="Стадия"
            value={stage}
            onChange={(e) => setFilters({ stage: isStage(e.target.value) ? e.target.value : undefined })}
          >
            <option value="">Все стадии</option>
            {STAGES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
          </NativeSelect>
        </div>
        <SearchInput
          value={q}
          onChange={(text) => setFilters({ q: text || undefined })}
          label="Поиск лидов"
          placeholder="Имя или username"
          className="w-full sm:w-72"
        />
      </PageHeader>
      {leads.isError ? (
        <ErrorState onRetry={() => void leads.refetch()} />
      ) : (
        <div className="rounded-xl border bg-card shadow-xs">
          <DataTable
            columns={LEAD_COLUMNS}
            data={rows}
            loading={leads.isPending}
            getRowId={(c) => String(c.id)}
            caption="Лиды"
            empty={
              <EmptyState
                icon={Users}
                title={filtered ? 'Никого не нашли' : 'Лидов пока нет'}
                hint={filtered ? 'Измените фильтры или поиск.' : 'Лид появится, когда напишет персонажу.'}
              />
            }
          />
        </div>
      )}
    </Page>
  );
}
