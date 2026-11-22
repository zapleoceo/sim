import { useMutation, useQueryClient } from '@tanstack/react-query';
import { CalendarClock, Loader2, Pencil, RefreshCw, Save, X } from 'lucide-react';
import { useMemo, useState } from 'react';
import { toast } from 'sonner';
import { InlineError } from '@/components/empty-state';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useDraft } from '@/hooks/use-draft';
import { qk } from '@/lib/query';
import type { DayEvent, PersonaDay } from '@/lib/types';
import { cn } from '@/lib/utils';
import { usePersonaId } from '../persona-scope';
import { lifeApi } from './life-api';
import { busyLabel, currentEventIndex, dayErrors, dayTitle, moodText, newDayEvent } from './life-logic';
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
  { key: 'mood', label: 'Настроение', kind: 'mood', width: '8rem' },
  { key: 'mood_why', label: 'Почему', kind: 'text', width: 'minmax(7rem,1fr)' },
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
        {moodText(event.mood, event.mood_why) && <Badge variant="warning">{moodText(event.mood, event.mood_why)}</Badge>}
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
  const personaId = usePersonaId();
  const events = useMemo(() => day?.events ?? [], [day]);
  // A re-plan arriving while the table is open but untouched shows up in it; an edit is kept.
  const { draft, setDraft, dirty, reset, commit } = useDraft<DayEvent[]>(events, { guardName: 'День' });
  const [editing, setEditing] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const current = nowMinute === null ? -1 : currentEventIndex(events, nowMinute);
  const close = (): void => {
    reset();
    setErrors([]);
    setEditing(false);
  };

  const plan = useMutation({
    mutationFn: () => lifeApi.planDay(personaId, date),
    onSuccess: () => {
      toast.success('Планирую день — он обновится через минуту');
      onPlanned();
    },
  });
  const save = useMutation({
    mutationFn: (next: DayEvent[]) => lifeApi.saveDay(personaId, date, next),
    onSuccess: async (saved) => {
      commit(saved.events);
      setEditing(false);
      await queryClient.invalidateQueries({ queryKey: qk.days(personaId) });
      toast.success('День сохранён');
    },
  });
  const titleId = `day-${date}`;

  return (
    // An open table needs the whole width: it spans both columns of the days grid.
    <section aria-labelledby={titleId} className={cn('flex min-w-0 flex-col gap-2 rounded-lg border p-3', editing && 'col-span-full')}>
      <header className="flex flex-wrap items-center gap-2">
        <h3 id={titleId} className="text-sm font-semibold">{label}: {dayTitle(date)}</h3>
        {sourceBadge(day)}
        <div className="ml-auto flex gap-1">
          {!editing && (
            <Button variant="ghost" size="sm" onClick={() => { reset(); setErrors([]); setEditing(true); }}>
              <Pencil />
              Изменить
            </Button>
          )}
          <Button variant="outline" size="sm" disabled={plan.isPending || editing} onClick={() => plan.mutate()}>
            {plan.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            Спланировать заново
          </Button>
        </div>
      </header>
      {day?.error && <InlineError>{day.error}</InlineError>}
      {day?.summary && <p className="text-sm text-muted-foreground">Как прошёл: {day.summary}</p>}
      {!editing ? (
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
          <RowsEditor title={`События: ${label}`} rows={draft} columns={EVENT_COLUMNS} onChange={setDraft}
            newRow={newDayEvent} addLabel="Добавить событие" emptyText="Событий нет." />
          {errors.length > 0 && <InlineError>{errors.join('; ')}</InlineError>}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={close}>
              <X />
              Отменить
            </Button>
            <Button
              size="sm"
              disabled={!dirty || save.isPending}
              onClick={() => {
                const found = dayErrors(draft);
                setErrors(found);
                if (!found.length) save.mutate(draft);
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
