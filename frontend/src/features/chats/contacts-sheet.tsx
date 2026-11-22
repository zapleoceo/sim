import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CalendarClock, Loader2, MessageSquare, Phone } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { ErrorState } from '@/components/empty-state';
import { Field } from '@/components/field';
import { Segmented } from '@/components/segmented';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { formatDateTime } from '@/lib/format';
import { qk } from '@/lib/query';
import type { Contact } from '@/lib/types';
import { chatsApi } from './api';
import {
  CONTACT_KINDS, DUE_NOW, QUICK_DUES, TOPIC_MAX, contactDraftError, contactStatus, dueIso, dueText, joinDue, splitDue,
  type ContactDraft,
} from './contacts-logic';

const newDraft = (): ContactDraft => ({ kind: 'message', due: DUE_NOW, topic: '' });

/** «Когда»: «Сейчас» by default, one-tap usual times, and a date and a time field for the rest. */
function DueField({ due, onChange }: { due: string; onChange: (due: string) => void }) {
  // The chip last tapped stays lit until the date or the time is changed by hand.
  const [pick, setPick] = useState<string | null>(null);
  const { date, time } = splitDue(due, new Date());
  const active = due === DUE_NOW ? 'now' : pick;
  return (
    <div className="flex flex-col gap-2">
      <div role="group" aria-label="Быстрый выбор времени" className="flex flex-wrap gap-1.5">
        {QUICK_DUES.map((q) => (
          <Button
            key={q.id}
            type="button"
            size="sm"
            variant={active === q.id ? 'default' : 'outline'}
            aria-pressed={active === q.id}
            onClick={() => {
              setPick(q.id);
              onChange(q.due(new Date()));
            }}
          >
            {q.label}
          </Button>
        ))}
      </div>
      <div className="flex gap-2">
        <Input aria-label="Дата" type="date" value={date} className="min-w-0 flex-1"
          onChange={(e) => {
            if (!e.target.value) return;
            setPick(null);
            onChange(joinDue(e.target.value, time));
          }} />
        <Input aria-label="Время" type="time" value={time} className="w-28"
          onChange={(e) => {
            if (!e.target.value) return;
            setPick(null);
            onChange(joinDue(date, e.target.value));
          }} />
      </div>
      <p className="text-xs text-muted-foreground">Отправится {dueText(due)} (время вашего браузера).</p>
    </div>
  );
}

/** Re-read the plan and the timeline (a planned contact leaves an «initiative» line there). */
function useRefreshContacts(chatId: number): () => void {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: qk.contacts(chatId) });
    void queryClient.invalidateQueries({ queryKey: qk.timeline(chatId) });
  };
}

function PlanForm({ chatId }: { chatId: number }) {
  const refresh = useRefreshContacts(chatId);
  const [draft, setDraft] = useState<ContactDraft>(newDraft);
  const [checked, setChecked] = useState(false);
  const error = checked ? contactDraftError(draft) : null;
  const plan = useMutation({
    mutationFn: (d: ContactDraft) =>
      chatsApi.planContact(chatId, { kind: d.kind, topic: d.topic.trim(), due_at: dueIso(d.due) as string }),
    onSuccess: (contact) => {
      toast.success(contact.kind === 'call' ? 'Звонок запланирован' : 'Сообщение запланировано');
      setDraft(newDraft());
      setChecked(false);
      refresh();
    },
  });
  const set = <K extends keyof ContactDraft>(key: K, value: ContactDraft[K]) => setDraft((d) => ({ ...d, [key]: value }));

  return (
    <form
      noValidate
      aria-label="Запланировать"
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        setChecked(true);
        if (!contactDraftError(draft)) plan.mutate(draft);
      }}
    >
      <Segmented label="Что сделать" value={draft.kind} options={CONTACT_KINDS} onChange={(v) => set('kind', v)} />
      <Field id="contact-due" label="Когда">
        <DueField due={draft.due} onChange={(due) => set('due', due)} />
      </Field>
      <Field id="contact-topic" label={draft.kind === 'call' ? 'О чём звонок' : 'О чём написать'} error={error ?? undefined}>
        <Textarea
          id="contact-topic"
          value={draft.topic}
          maxLength={TOPIC_MAX}
          onChange={(e) => set('topic', e.target.value)}
          placeholder={draft.kind === 'call' ? 'Например: договорились созвониться после работы' : 'Например: спросить, как прошло собеседование'}
          className="min-h-20"
          aria-invalid={error ? true : undefined}
        />
      </Field>
      <Button type="submit" className="self-end" disabled={plan.isPending}>
        {plan.isPending ? <Loader2 className="animate-spin" /> : <CalendarClock />}
        Запланировать
      </Button>
    </form>
  );
}

function ContactRow({ chatId, contact }: { chatId: number; contact: Contact }) {
  const refresh = useRefreshContacts(chatId);
  const cancel = useMutation({
    mutationFn: () => chatsApi.cancelContact(chatId, contact.id),
    onSuccess: () => {
      toast.success('Отменено');
      refresh();
    },
  });
  const status = contactStatus(contact.status);
  const KindIcon = contact.kind === 'call' ? Phone : MessageSquare;
  return (
    <li className="flex flex-col gap-1.5 rounded-lg border p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <KindIcon className="size-4 flex-none text-muted-foreground" aria-label={contact.kind === 'call' ? 'Звонок' : 'Сообщение'} />
        <time dateTime={contact.due_at} className="font-medium tabular">{formatDateTime(contact.due_at)}</time>
        {contact.source === 'persona' && <Badge variant="outline" title="Она решила сама">сама</Badge>}
        <Badge variant={status.variant} className="ml-auto">{status.label}</Badge>
      </div>
      <p className="break-words whitespace-pre-wrap">{contact.topic}</p>
      {contact.result && <p className="break-words whitespace-pre-wrap text-xs text-muted-foreground">{contact.result}</p>}
      {contact.status === 'planned' && (
        <Button
          variant="ghost-destructive"
          size="sm"
          className="self-end"
          disabled={cancel.isPending}
          onClick={() => cancel.mutate()}
        >
          Отменить
        </Button>
      )}
    </li>
  );
}

function ContactList({ chatId }: { chatId: number }) {
  const contacts = useQuery({ queryKey: qk.contacts(chatId), queryFn: () => chatsApi.contacts(chatId), staleTime: 0 });
  if (contacts.isPending) {
    return (
      <div className="flex flex-col gap-2" aria-label="Загрузка плана">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    );
  }
  if (contacts.isError) return <ErrorState onRetry={() => void contacts.refetch()} />;
  if (contacts.data.items.length === 0) {
    return <p className="text-sm text-muted-foreground">Пока ничего не запланировано.</p>;
  }
  return (
    <ul className="flex flex-col gap-2" aria-label="Запланированные">
      {contacts.data.items.map((c) => <ContactRow key={c.id} chatId={chatId} contact={c} />)}
    </ul>
  );
}

/**
 * «Запланировать» in the chat header (docs/calls.md): the manager plans that she writes or calls
 * this client at a time about a topic; below — what is planned here, by the manager or by herself.
 */
export function ContactsSheet({ chatId }: { chatId: number }) {
  return (
    <Sheet>
      <SheetTrigger asChild>
        <Button variant="ghost" size="sm" title="Запланировать сообщение или звонок">
          <CalendarClock />
          <span className="max-sm:sr-only">Запланировать</span>
        </Button>
      </SheetTrigger>
      <SheetContent className="overflow-y-auto p-4 sm:max-w-md">
        <SheetHeader className="p-0 pr-8">
          <SheetTitle>Запланировано</SheetTitle>
          <SheetDescription>
            Она напишет или позвонит в это время и сама заведёт разговор на эту тему.
          </SheetDescription>
        </SheetHeader>
        <PlanForm chatId={chatId} />
        <div className="flex flex-col gap-2 border-t pt-4">
          <h3 className="text-sm font-semibold">План этого чата</h3>
          <ContactList chatId={chatId} />
        </div>
      </SheetContent>
    </Sheet>
  );
}
