import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BookOpen, IdCard, MessagesSquare, RefreshCw, Save, Undo2 } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { useConfirm } from '@/components/confirm';
import { EmptyState, ErrorState, InlineError } from '@/components/empty-state';
import { Field } from '@/components/field';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { NativeSelect } from '@/components/ui/native-select';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { useUnsavedGuard } from '@/hooks/use-unsaved-guard';
import { qk } from '@/lib/query';
import type { ChatDay, ChatDayPatch, ChatWeek, ClientDetail } from '@/lib/types';
import { daysApi } from './api';
import {
  DAY_STATE_LABEL,
  type DayDraft,
  canRebuild,
  chatDayTitle,
  chatOptionLabel,
  chatWeekTitle,
  dayPatch,
  dayState,
  toDraft,
} from './days-logic';

const STATE_VARIANT = { model: 'success', edited: 'warning', error: 'destructive' } as const;

/** One day: the chronicler's summary and facts, editable; «Пересобрать день». */
function DayItem({ chatId, day, today }: { chatId: number; day: ChatDay; today: string }) {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  // Local state for the text being edited: a live refresh never overwrites typing.
  const [draft, setDraft] = useState<DayDraft>(() => toDraft(day));
  const patch = dayPatch(draft, day);
  const dirty = draft.summary !== day.summary || draft.facts !== toDraft(day).facts;
  useUnsavedGuard(dirty);
  const state = dayState(day);
  const title = chatDayTitle(day);

  const save = useMutation({
    mutationFn: (body: ChatDayPatch) => daysApi.save(chatId, day.day, body),
    onSuccess: () => {
      toast.success('Итог дня сохранён: ночью Летописец его не тронет');
      void queryClient.invalidateQueries({ queryKey: qk.chatDays(chatId) });
    },
  });
  const rebuild = useMutation({
    mutationFn: () => daysApi.rebuild(chatId, day.day),
    onSuccess: () => toast.success('Летописец пересобирает день — итог обновится через пару минут'),
  });

  const askRebuild = async () => {
    if (state === 'edited' && !(await confirm({
      title: 'Пересобрать день?',
      message: 'Летописец напишет итог заново, ваша правка пропадёт.',
      confirmLabel: 'Пересобрать',
      danger: true,
    }))) return;
    rebuild.mutate();
  };

  const summaryId = `day-${day.day}-summary`;
  const factsId = `day-${day.day}-facts`;
  return (
    <li className="flex flex-col gap-3 rounded-xl border p-3 sm:p-4" aria-label={title}>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold first-letter:uppercase">{title}</h3>
        <Badge variant={STATE_VARIANT[state]}>{DAY_STATE_LABEL[state]}</Badge>
        <Button
          variant="outline"
          size="sm"
          className="ml-auto"
          disabled={!canRebuild(day.day, today) || rebuild.isPending}
          onClick={() => void askRebuild()}
        >
          <RefreshCw />
          Пересобрать день
        </Button>
      </div>
      {state === 'error' && <InlineError>{day.error}</InlineError>}
      <Field id={summaryId} label="Итог дня">
        <Textarea
          id={summaryId}
          className="min-h-28"
          value={draft.summary}
          onChange={(e) => setDraft({ ...draft, summary: e.target.value })}
        />
      </Field>
      <Field id={factsId} label="Факты" hint="По одному на строку: что о нём стало известно, договорённости.">
        <Textarea
          id={factsId}
          className="min-h-16"
          value={draft.facts}
          onChange={(e) => setDraft({ ...draft, facts: e.target.value })}
        />
      </Field>
      {dirty && (
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={() => setDraft(toDraft(day))}>
            <Undo2 />
            Отменить
          </Button>
          <Button disabled={!patch || save.isPending} onClick={() => patch && save.mutate(patch)}>
            <Save />
            Сохранить
          </Button>
        </div>
      )}
    </li>
  );
}

function WeekItem({ week }: { week: ChatWeek }) {
  return (
    <li className="flex flex-col gap-1 rounded-lg border px-3 py-2">
      <span className="text-sm font-medium">{chatWeekTitle(week)}</span>
      <p className="text-sm whitespace-pre-wrap">{week.summary || week.error || '—'}</p>
    </li>
  );
}

function ChatDays({ chatId }: { chatId: number }) {
  const days = useQuery({ queryKey: qk.chatDays(chatId), queryFn: () => daysApi.list(chatId) });
  if (days.isPending) {
    return (
      <div className="flex flex-col gap-3">
        <Skeleton className="h-32 w-full rounded-xl" />
        <Skeleton className="h-32 w-full rounded-xl" />
      </div>
    );
  }
  if (days.isError) return <ErrorState onRetry={() => void days.refetch()} />;
  const { items, weeks, today } = days.data;
  return (
    <div className="flex flex-col gap-6">
      {items.length === 0 ? (
        <EmptyState
          icon={BookOpen}
          title="Итогов дней пока нет"
          hint="Летописец пишет итог дня в её полночь; сегодняшний день она помнит дословно."
        />
      ) : (
        <ol className="flex flex-col gap-3">
          {items.map((day) => (
            // A new version from the server (rebuilt, edited elsewhere) starts a fresh draft.
            <DayItem key={`${day.day}:${day.updated_at}`} chatId={chatId} day={day} today={today} />
          ))}
        </ol>
      )}
      {weeks.length > 0 && (
        <section className="flex flex-col gap-2">
          <h3 className="text-sm font-semibold">Итоги недель</h3>
          <ol className="flex flex-col gap-2">
            {weeks.map((week) => <WeekItem key={week.week} week={week} />)}
          </ol>
        </section>
      )}
    </div>
  );
}

/** «Дни»: what she remembers of each day with this lead, per chat (docs/memory.md). */
export function DaysTab({ client }: { client: ClientDetail }) {
  const [chosen, setChosen] = useState<number | null>(null);
  const chatId = chosen ?? client.chats[0]?.id ?? null;
  const refresh = useMutation({
    mutationFn: (id: number) => daysApi.refreshPassport(id),
    onSuccess: () => toast.success('Паспортистка обновляет паспорт — он изменится через пару минут'),
  });

  if (chatId === null) return <EmptyState icon={MessagesSquare} title="Чатов нет" />;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3">
        {client.chats.length > 1 && (
          <Field id="days-chat" label="Чат" className="w-full sm:w-72">
            <NativeSelect id="days-chat" value={chatId} onChange={(e) => setChosen(Number(e.target.value))}>
              {client.chats.map((chat) => (
                <option key={chat.id} value={chat.id}>{chatOptionLabel(chat)}</option>
              ))}
            </NativeSelect>
          </Field>
        )}
        <Button variant="outline" className="sm:ml-auto" disabled={refresh.isPending} onClick={() => refresh.mutate(chatId)}>
          <IdCard />
          Обновить паспорт сейчас
        </Button>
      </div>
      <ChatDays key={chatId} chatId={chatId} />
    </div>
  );
}
