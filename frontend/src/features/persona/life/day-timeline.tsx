import { useMutation, useQueryClient } from '@tanstack/react-query';
import { CalendarClock, Loader2, Pencil, RefreshCw, Save, X } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { InlineError } from '@/components/empty-state';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useUnsavedGuard } from '@/hooks/use-unsaved-guard';
import { qk } from '@/lib/query';
import type { DayEvent, PersonaDay } from '@/lib/types';
import { cn } from '@/lib/utils';
import { lifeApi } from './life-api';
import { busyLabel, currentEventIndex, dayErrors, dayTitle, newDayEvent, sameJson } from './life-logic';
import { RowsEditor, type Column } from './rows-editor';

const EVENT_COLUMNS: Column<DayEvent>[] = [
  { key: 'start', label: 'С', kind: 'time', width: '6.5rem' },
  { key: 'end', label: 'До', kind: 'time', width: '6.5rem' },
  { key: 'title', label: 'Что делает', kind: 'text', width: 'minmax(9rem,1.5fr)' },
  { key: 'place', label: 'Где', kind: 'text', width: 'minmax(7rem,1fr)' },
  { key: 'with_whom', label: 'С кем', kind: 'names', width: 'minmax(6rem,1fr)' },
  { key: 'busy', label: 'Занятость', kind: 'busy', width: '8rem' },
  { key: 'outfit', label: 'Одежда', kind: 'textarea', width: 'minmax(8rem,1fr)' },
  { key: 'ambience', label: 'Звук', kind: 'ambience', width: '7.5rem' },
  { key: 'details', label: 'Подробности', kind: 'textarea', width: 'minmax(9rem,1.5fr)' },
];

function sourceBadge(day: PersonaDay | undefined) {
  if (!day || (!day.events.length && !day.error)) return <Badge variant="muted">не спланирован</Badge>;
  if (day.error && !day.events.length) return <Badge variant="destructive">ошибка плана</Badge>;
  return day.source === 'edited' ? <Badge variant="warning">изменён вручную</Badge> : <Badge variant="success">план модели</Badge>;
}

function EventRow({ event, current }: { event: DayEvent; current: boolean }) {
  return (
    <li
      aria-current={current ? 'true' : undefined}
      className={cn('flex flex-col gap-0.5 rounded-md border-l-2 px-3 py-2', current ? 'border-primary bg-primary/8' : 'border-transparent')}
    >
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="font-mono text-xs text-muted-foreground tabular-nums">{event.start}–{event.end}</span>
        <span className="text-sm font-medium">{event.title}</span>
        {current && <Badge variant="default">сейчас</Badge>}
        {event.busy !== 'free' && <Badge variant="muted">{busyLabel(event.busy)}</Badge>}
      </div>
      <span className="text-xs text-muted-foreground">
        {[event.place, event.with_whom.length ? `с ${event.with_whom.join(', ')}` : '', event.outfit].filter(Boolean).join(' · ')}
      </span>
      {event.details && <span className="text-xs">{event.details}</span>}
    </li>
  );
}

/** One day of her life: the plan, the current event, re-plan and manual edit. */
export function DayCard({ label, date, day, nowMinute, onPlanned }: {
  label: string;
  date: string;
  day: PersonaDay | undefined;
  /** Minute of her local day when this is today, else null. */
  nowMinute: number | null;
  onPlanned: () => void;
}) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<DayEvent[] | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const events = day?.events ?? [];
  const dirty = editing !== null && !sameJson(editing, events);
  useUnsavedGuard(dirty);
  const current = nowMinute === null ? -1 : currentEventIndex(events, nowMinute);

  const plan = useMutation({
    mutationFn: () => lifeApi.planDay(date),
    onSuccess: () => {
      toast.success('Планирую день — он обновится через минуту');
      onPlanned();
    },
  });
  const save = useMutation({
    mutationFn: (next: DayEvent[]) => lifeApi.saveDay(date, next),
    onSuccess: async () => {
      setEditing(null);
      await queryClient.invalidateQueries({ queryKey: qk.days });
      toast.success('День сохранён');
    },
  });
  const titleId = `day-${date}`;

  return (
    <section aria-labelledby={titleId} className="flex min-w-0 flex-col gap-2 rounded-lg border p-3">
      <header className="flex flex-wrap items-center gap-2">
        <h3 id={titleId} className="text-sm font-semibold">{label}: {dayTitle(date)}</h3>
        {sourceBadge(day)}
        <div className="ml-auto flex gap-1">
          {editing === null && (
            <Button variant="ghost" size="sm" onClick={() => { setEditing(events); setErrors([]); }}>
              <Pencil />
              Изменить
            </Button>
          )}
          <Button variant="outline" size="sm" disabled={plan.isPending || editing !== null} onClick={() => plan.mutate()}>
            {plan.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            Спланировать заново
          </Button>
        </div>
      </header>
      {day?.error && <InlineError>{day.error}</InlineError>}
      {day?.summary && <p className="text-sm text-muted-foreground">Как прошёл: {day.summary}</p>}
      {editing === null ? (
        events.length ? (
          <ol className="flex flex-col gap-1">
            {events.map((event, i) => <EventRow key={`${event.start}-${i}`} event={event} current={i === current} />)}
          </ol>
        ) : (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <CalendarClock className="size-4" />
            Плана нет — она живёт по распорядку.
          </p>
        )
      ) : (
        <>
          <RowsEditor title={`События: ${label}`} rows={editing} columns={EVENT_COLUMNS} onChange={setEditing}
            newRow={newDayEvent} addLabel="Добавить событие" emptyText="Событий нет." />
          {errors.length > 0 && <InlineError>{errors.join('; ')}</InlineError>}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setEditing(null)}>
              <X />
              Отменить
            </Button>
            <Button
              size="sm"
              disabled={!dirty || save.isPending}
              onClick={() => {
                const found = dayErrors(editing);
                setErrors(found);
                if (!found.length) save.mutate(editing);
              }}
            >
              <Save />
              Сохранить день
            </Button>
          </div>
        </>
      )}
    </section>
  );
}
