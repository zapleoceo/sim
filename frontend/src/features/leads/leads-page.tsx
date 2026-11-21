import { useQuery } from '@tanstack/react-query';
import { Link, useSearch } from '@tanstack/react-router';
import type { ColumnDef } from '@tanstack/react-table';
import { Search, Users } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Avatar } from '@/components/avatar';
import { DataTable } from '@/components/data-table';
import { EmptyState, ErrorState } from '@/components/empty-state';
import { Page, PageHeader } from '@/components/page';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { NativeSelect } from '@/components/ui/native-select';
import { personaApi } from '@/features/persona/api';
import { useDebounced } from '@/hooks/use-debounced';
import { formatDateTime, formatShort } from '@/lib/format';
import { qk } from '@/lib/query';
import type { Lead, LeadStage } from '@/lib/types';
import { clientName } from '@/lib/utils';
import { leadsApi } from './api';
import { ClientAiSwitch } from './client-ai-switch';
import { STAGES, stageInfo } from './passport-logic';


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

/** «Лиды»: every client of every persona, filters by persona and stage, search; a click opens the card. */
export function LeadsPage() {
  // `/leads?persona=2` (from «Персонажи») preselects a persona; then the filters live here.
  const search = useSearch({ strict: false }) as { persona?: number; stage?: LeadStage };
  const [text, setText] = useState('');
  const q = useDebounced(text.trim(), 300);
  const [personaId, setPersonaId] = useState<number>(search.persona ?? 0);
  const [stage, setStage] = useState<LeadStage | ''>(search.stage ?? '');
  const filters = { q, personaId, stage };
  const personas = useQuery({ queryKey: qk.personaList, queryFn: personaApi.list, staleTime: 60_000 });
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
          <NativeSelect aria-label="Персонаж" value={String(personaId)} onChange={(e) => setPersonaId(Number(e.target.value) || 0)}>
            <option value="0">Все персонажи</option>
            {(personas.data?.items ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </NativeSelect>
        </div>
        <div className="w-[calc(50%-0.25rem)] sm:w-40">
          <NativeSelect aria-label="Стадия" value={stage} onChange={(e) => setStage(e.target.value as LeadStage | '')}>
            <option value="">Все стадии</option>
            {STAGES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
          </NativeSelect>
        </div>
        <label className="relative w-full sm:w-72">
          <span className="sr-only">Поиск лидов</span>
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input type="search" value={text} onChange={(e) => setText(e.target.value)} placeholder="Имя или username" className="pl-8" />
        </label>
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
