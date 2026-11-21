import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate } from '@tanstack/react-router';
import type { ColumnDef } from '@tanstack/react-table';
import { Copy, Smile } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Avatar } from '@/components/avatar';
import { DataTable } from '@/components/data-table';
import { EmptyState, ErrorState } from '@/components/empty-state';
import { Page, PageHeader } from '@/components/page';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { qk } from '@/lib/query';
import type { PersonaRow } from '@/lib/types';
import { personaApi } from './api';
import { channelStatus } from './channel-logic';
import { CloneDialog } from './clone-dialog';

export const avatarUrl = (mediaId: number | null): string | null => (mediaId ? `/api/media/${mediaId}/file` : null);

/** The state of a persona's Telegram account(s), one line. */
export function channelSummary(row: PersonaRow): { label: string; variant: 'success' | 'destructive' | 'muted' | 'warning' } {
  if (row.channels.length === 0) return { label: 'нет канала', variant: 'warning' };
  const best = row.channels.find((c) => c.status === 'connected') ?? row.channels[0];
  const status = channelStatus(best?.status ?? 'new');
  return { label: best?.account_label ? `${best.account_label} · ${status.label}` : status.label, variant: status.variant };
}

function columns(onClone: (row: PersonaRow) => void): ColumnDef<PersonaRow, unknown>[] {
  return [
    {
      id: 'name',
      header: 'Персонаж',
      cell: ({ row }) => (
        <Link
          to="/personas/$personaId"
          params={{ personaId: String(row.original.id) }}
          className="flex min-w-0 items-center gap-2.5 font-medium hover:underline"
        >
          <Avatar name={row.original.name} url={avatarUrl(row.original.avatar_media_id)} size={32} />
          <span className="truncate">{row.original.name}</span>
          {!row.original.is_active && <Badge variant="muted">в архиве</Badge>}
        </Link>
      ),
      meta: { className: 'w-full max-w-0 md:w-auto md:max-w-none' },
    },
    {
      id: 'channel',
      header: 'Канал',
      cell: ({ row }) => {
        const summary = channelSummary(row.original);
        return <Badge variant={summary.variant}>{summary.label}</Badge>;
      },
      meta: { className: 'hidden sm:table-cell' },
    },
    {
      id: 'leads',
      header: 'Лиды',
      cell: ({ row }) => (
        <Link to="/leads" search={{ persona: row.original.id }} className="hover:underline">
          {row.original.leads_count}
        </Link>
      ),
      meta: { className: 'text-right tabular' },
    },
    {
      id: 'ai',
      header: 'ИИ',
      cell: ({ row }) => (
        <Badge variant={row.original.ai_enabled && row.original.is_active ? 'success' : 'muted'}>
          {row.original.ai_enabled && row.original.is_active ? 'отвечает' : 'выключен'}
        </Badge>
      ),
      meta: { className: 'hidden md:table-cell' },
    },
    {
      id: 'clone',
      header: () => <span className="sr-only">Действия</span>,
      cell: ({ row }) => (
        <Button variant="ghost" size="sm" onClick={() => onClone(row.original)} aria-label={`Клонировать ${row.original.name}`}>
          <Copy />
          <span className="max-sm:sr-only">Клонировать</span>
        </Button>
      ),
      meta: { className: 'w-0 text-right' },
    },
  ];
}

/** «Персонажи»: every persona with her channel, leads and AI state; a click opens her page. */
export function PersonasPage() {
  const navigate = useNavigate();
  const personas = useQuery({ queryKey: qk.personaList, queryFn: personaApi.list });
  const [source, setSource] = useState<PersonaRow | null>(null);
  const rows = useMemo(() => personas.data?.items ?? [], [personas.data]);
  const cols = useMemo(() => columns(setSource), []);

  return (
    <Page width="wide">
      <PageHeader title="Персонажи" subtitle={personas.data ? `${rows.length} в списке` : undefined} />
      {personas.isError ? (
        <ErrorState onRetry={() => void personas.refetch()} />
      ) : (
        <div className="rounded-xl border bg-card shadow-xs">
          <DataTable
            columns={cols}
            data={rows}
            loading={personas.isPending}
            getRowId={(p) => String(p.id)}
            caption="Персонажи"
            rowClassName={(row) => (row.original.is_active ? undefined : 'opacity-70')}
            empty={<EmptyState icon={Smile} title="Персонажей пока нет" hint="Первый персонаж создаётся при установке." />}
          />
        </div>
      )}
      {source && (
        <CloneDialog
          source={source}
          takenNames={rows.map((r) => r.name)}
          open={source !== null}
          onOpenChange={(open) => {
            if (!open) setSource(null);
          }}
          onCloned={(persona) => void navigate({ to: '/personas/$personaId', params: { personaId: String(persona.id) } })}
        />
      )}
    </Page>
  );
}
