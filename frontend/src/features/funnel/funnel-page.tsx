import {
  DndContext, DragOverlay, KeyboardSensor, MouseSensor, TouchSensor, useDraggable, useDroppable, useSensor, useSensors,
  type DragEndEvent, type DragStartEvent,
} from '@dnd-kit/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { ArrowRightLeft, Lock } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { Avatar } from '@/components/avatar';
import { StatusBadge } from '@/components/badges';
import { ErrorState } from '@/components/empty-state';
import { FilterBar, FilterField } from '@/components/filter-bar';
import { OptionSelect } from '@/components/option-select';
import { Page, PageHeader } from '@/components/page';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Skeleton } from '@/components/ui/skeleton';
import { clientsApi } from '@/lib/api/clients';
import { personasApi } from '@/lib/api/personas';
import { checkStageDrop } from '@/lib/funnel-rules';
import type { ClientMini, FunnelResponse, Stage } from '@/lib/models';
import { qk } from '@/lib/query';
import { STATUSES, STATUS_LABELS } from '@/lib/stages';
import { cn } from '@/lib/utils';
import { MOVE_TARGETS, buildColumns, moveInFunnel, type FunnelColumn } from './funnel-logic';

interface DragData { client: ClientMini; from: Stage }

const cardClass =
  'flex flex-col gap-2 rounded-lg border bg-card p-2.5 shadow-xs outline-none focus-visible:ring-[3px] focus-visible:ring-ring';

function DraggableCard({
  client,
  column,
  onMove,
}: {
  client: ClientMini;
  column: FunnelColumn;
  onMove: (client: ClientMini, from: Stage, to: Stage) => void;
}) {
  const data: DragData = { client, from: column.stage };
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: `client-${client.id}`,
    data,
    disabled: column.locked,
  });
  return (
    <article
      ref={setNodeRef}
      {...attributes}
      {...listeners}
      aria-roledescription={column.locked ? undefined : 'перетаскиваемая карточка'}
      className={cn(
        cardClass,
        !column.locked && 'cursor-grab touch-manipulation active:cursor-grabbing',
        isDragging && 'opacity-40',
      )}
    >
      <CardBody client={client} column={column} onMove={onMove} />
    </article>
  );
}

function CardBody({
  client,
  column,
  onMove,
  overlay = false,
}: {
  client: ClientMini;
  column: FunnelColumn;
  onMove: (client: ClientMini, from: Stage, to: Stage) => void;
  overlay?: boolean;
}) {
  return (
    <>
      <div className="flex items-center gap-2">
        <Link
          to="/clients/$id"
          params={{ id: String(client.id) }}
          onKeyDown={(e) => e.stopPropagation()}
          className="flex min-w-0 flex-1 items-center gap-2 text-sm font-medium hover:underline"
        >
          <Avatar name={client.display_name} url={client.avatar_url} size={26} />
          <span className="truncate">{client.display_name}</span>
        </Link>
        {!column.locked && !overlay && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                className="size-7 flex-none"
                aria-label={`Переместить: ${client.display_name}`}
                onPointerDown={(e) => e.stopPropagation()}
                onKeyDown={(e) => e.stopPropagation()}
              >
                <ArrowRightLeft />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">Переместить на этап</DropdownMenuLabel>
              {MOVE_TARGETS.map((s) => (
                <DropdownMenuItem
                  key={s.id}
                  disabled={s.id === column.stage}
                  onSelect={() => onMove(client, column.stage, s.id)}
                >
                  <span className="size-2 rounded-full" style={{ background: s.color }} />
                  {s.label}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>
      {client.status !== 'active' && <StatusBadge status={client.status} />}
    </>
  );
}

function Column({
  column,
  onMove,
}: {
  column: FunnelColumn;
  onMove: (client: ClientMini, from: Stage, to: Stage) => void;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: column.stage });
  return (
    <section
      aria-label={column.label}
      data-stage={column.stage}
      className={cn(
        'flex w-[78vw] max-w-72 flex-none snap-start flex-col rounded-xl border bg-muted/40 sm:w-64',
        isOver && !column.locked && 'border-primary/60 bg-accent/60',
        isOver && column.locked && 'border-destructive/50',
      )}
    >
      <header className="flex items-center gap-2 border-b px-3 py-2.5">
        <span className="size-2 rounded-full" style={{ background: column.color }} />
        <h2 className="flex-1 truncate text-sm font-medium">{column.label}</h2>
        {column.locked && <Lock className="size-3.5 text-muted-foreground" aria-label="только данные партнёра" />}
        <span className="rounded-md bg-background px-1.5 text-xs text-muted-foreground tabular">{column.count}</span>
      </header>
      <div ref={setNodeRef} className="flex min-h-24 flex-1 flex-col gap-2 overflow-y-auto p-2 md:max-h-[calc(100dvh-20rem)]">
        {column.clients.map((c) => (
          <DraggableCard key={c.id} client={c} column={column} onMove={onMove} />
        ))}
        {column.clients.length === 0 && <p className="py-6 text-center text-xs text-muted-foreground">Пусто</p>}
      </div>
    </section>
  );
}

export function FunnelPage() {
  const queryClient = useQueryClient();
  const [personaId, setPersonaId] = useState<number | null>(null);
  const [dragging, setDragging] = useState<DragData | null>(null);

  const personas = useQuery({ queryKey: qk.personas, queryFn: personasApi.list });
  const funnel = useQuery({ queryKey: qk.funnel(personaId), queryFn: () => clientsApi.funnel(personaId) });
  const columns = buildColumns(funnel.data);

  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 220, tolerance: 6 } }),
    useSensor(KeyboardSensor),
  );

  const moveMutation = useMutation({
    mutationFn: ({ client, to }: { client: ClientMini; from: Stage; to: Stage }) =>
      clientsApi.changeStage(client.id, { stage: to }),
    onMutate: async ({ client, from, to }) => {
      const key = qk.funnel(personaId);
      await queryClient.cancelQueries({ queryKey: key });
      const snapshot = queryClient.getQueryData<FunnelResponse>(key);
      if (snapshot) queryClient.setQueryData(key, moveInFunnel(snapshot, client, from, to));
      return { key, snapshot };
    },
    // Server refused the move: put the card back (the toast comes from the HTTP layer).
    onError: (_e, _v, ctx) => {
      if (ctx?.snapshot) queryClient.setQueryData(ctx.key, ctx.snapshot);
    },
  });

  const move = (client: ClientMini, from: Stage, to: Stage): void => {
    const verdict = checkStageDrop(from, to);
    if (!verdict.ok) {
      if (verdict.reason) toast(verdict.reason);
      return;
    }
    moveMutation.mutate({ client, from, to });
  };

  const onDragStart = (e: DragStartEvent): void => setDragging((e.active.data.current as DragData | undefined) ?? null);
  const onDragEnd = (e: DragEndEvent): void => {
    setDragging(null);
    const data = e.active.data.current as DragData | undefined;
    if (!data || !e.over) return;
    move(data.client, data.from, e.over.id as Stage);
  };

  const draggingColumn = dragging ? columns.find((c) => c.stage === dragging.from) : undefined;

  return (
    <Page width="wide">
      <PageHeader title="Воронка" subtitle="Перетаскивайте карточки между этапами. Регистрацию и оплату ставит только партнёр." />

      <FilterBar
        activeCount={personaId === null ? 0 : 1}
        filters={
          <FilterField label="Персонаж">
            <OptionSelect
              value={personaId}
              onChange={setPersonaId}
              options={[{ value: null, label: 'Все персонажи' }, ...(personas.data ?? []).map((p) => ({ value: p.id, label: p.name }))]}
            />
          </FilterField>
        }
      />

      {funnel.data && (
        <ul aria-label="Статусы клиентов" className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {STATUSES.map((s) => (
            <li key={s} className="rounded-xl border bg-card px-4 py-3">
              <strong className="block text-xl font-semibold tabular">{funnel.data.statuses[s] ?? 0}</strong>
              <span className="text-sm text-muted-foreground">{STATUS_LABELS[s]}</span>
            </li>
          ))}
        </ul>
      )}

      {funnel.isError ? (
        <ErrorState onRetry={() => void funnel.refetch()} />
      ) : funnel.isPending ? (
        <div className="flex gap-3 overflow-hidden">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-64 w-64 flex-none rounded-xl" />
          ))}
        </div>
      ) : (
        <DndContext sensors={sensors} onDragStart={onDragStart} onDragEnd={onDragEnd} onDragCancel={() => setDragging(null)}>
          <div className="-mx-3 flex snap-x snap-mandatory gap-3 overflow-x-auto px-3 pb-3 scrollbar-thin sm:-mx-6 sm:px-6">
            {columns.map((col) => (
              <Column key={col.stage} column={col} onMove={move} />
            ))}
          </div>
          <DragOverlay>
            {dragging && draggingColumn ? (
              <article className={cn(cardClass, 'rotate-1 shadow-lg')}>
                <CardBody client={dragging.client} column={draggingColumn} onMove={move} overlay />
              </article>
            ) : null}
          </DragOverlay>
        </DndContext>
      )}
    </Page>
  );
}
