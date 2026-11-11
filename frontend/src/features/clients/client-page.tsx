import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getRouteApi, Link, useLocation } from '@tanstack/react-router';
import { ArrowRightLeft, BadgeCheck, CreditCard, History, Hourglass, Lock, UserX } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { Avatar } from '@/components/avatar';
import { ChannelIcon, StatusBadge } from '@/components/badges';
import { EmptyState, ErrorState } from '@/components/empty-state';
import { Field, FormGrid } from '@/components/field';
import { OptionSelect } from '@/components/option-select';
import { BackLink, Page } from '@/components/page';
import { Button } from '@/components/ui/button';
import { Card, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Tip } from '@/components/ui/tooltip';
import { clientsApi } from '@/lib/api/clients';
import { ApiError } from '@/lib/api/http';
import { formatDateTime, isoToLocalInput, localInputToIso } from '@/lib/format';
import type { ClientCard, ClientStatus, Stage } from '@/lib/models';
import { qk } from '@/lib/query';
import { PARTNER_ONLY_HINT, STAGES, STATUSES, STATUS_LABELS, stageMeta } from '@/lib/stages';
import { cn, positiveInt } from '@/lib/utils';
import { ActivityFeed } from '../activity/activity-feed';
import { timelineWithDates, type ClientFormValues, type TimelineIcon } from './client-logic';

const route = getRouteApi('/_app/clients/$id');

const TIMELINE_ICONS: Record<TimelineIcon, typeof History> = {
  stage: ArrowRightLeft,
  payment: CreditCard,
  registration: BadgeCheck,
};

export function formValuesOf(c: ClientCard): ClientFormValues {
  return {
    display_name: c.display_name,
    next_step: c.next_step ?? '',
    next_step_at: isoToLocalInput(c.next_step_at),
    notes: c.notes ?? '',
  };
}

function ClientForm({ card }: { card: ClientCard }) {
  const queryClient = useQueryClient();
  const initial = formValuesOf(card);
  const [values, setValues] = useState<ClientFormValues>(initial);
  const pristine = JSON.stringify(values) === JSON.stringify(initial);
  const set = (key: keyof ClientFormValues, v: string): void => setValues((s) => ({ ...s, [key]: v }));

  const save = useMutation({
    mutationFn: () =>
      clientsApi.patch(card.id, {
        display_name: values.display_name.trim() || card.display_name,
        notes: values.notes,
        next_step: values.next_step,
        next_step_at: localInputToIso(values.next_step_at),
      }),
    onSuccess: (updated) => {
      const next = { ...card, ...updated };
      queryClient.setQueryData(qk.client(card.id), next);
      setValues(formValuesOf(next));
      toast.success('Сохранено');
    },
  });

  return (
    <Card>
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <CardTitle>Работа с клиентом</CardTitle>
        <FormGrid>
          <Field label="Имя">
            <Input value={values.display_name} onChange={(e) => set('display_name', e.target.value)} />
          </Field>
          <Field label="Следующий шаг">
            <Input
              value={values.next_step}
              placeholder="Напр.: напомнить про ссылку"
              onChange={(e) => set('next_step', e.target.value)}
            />
          </Field>
          <Field label="Когда">
            <Input type="datetime-local" value={values.next_step_at} onChange={(e) => set('next_step_at', e.target.value)} />
          </Field>
          {card.needs.length > 0 && (
            <div className="flex flex-col gap-1.5">
              <span className="text-sm font-medium">Потребности</span>
              <span className="flex flex-wrap gap-1.5">
                {card.needs.map((n) => (
                  <span key={n} className="rounded-md bg-muted px-2 py-0.5 text-xs">
                    {n}
                  </span>
                ))}
              </span>
            </div>
          )}
          <Field label="Заметки" className="col-span-full">
            <Textarea rows={4} value={values.notes} onChange={(e) => set('notes', e.target.value)} className="min-h-24" />
          </Field>
        </FormGrid>
        <div className="flex justify-end">
          <Button type="submit" disabled={save.isPending || pristine}>
            Сохранить
          </Button>
        </div>
      </form>
    </Card>
  );
}

function StageCard({ card, onChanged }: { card: ClientCard; onChanged: () => void }) {
  const change = useMutation({
    mutationFn: (body: { stage?: Stage; status?: ClientStatus }) => clientsApi.changeStage(card.id, body),
    onSuccess: onChanged,
  });

  const setStage = (stage: Stage): void => {
    if (stage === card.stage) return;
    const meta = stageMeta(stage);
    if (!meta.aiReachable) {
      toast(`Этап «${meta.label}» ${PARTNER_ONLY_HINT}`);
      return;
    }
    change.mutate({ stage });
  };

  return (
    <Card>
      <CardTitle id="stage-h">Этап воронки</CardTitle>
      <div role="group" aria-labelledby="stage-h" className="flex flex-wrap gap-1.5">
        {STAGES.map((s) => {
          const current = s.id === card.stage;
          return (
            <Tip key={s.id} label={s.aiReachable ? '' : PARTNER_ONLY_HINT}>
              <button
                type="button"
                aria-pressed={current}
                aria-disabled={!s.aiReachable || undefined}
                onClick={() => setStage(s.id)}
                className={cn(
                  'inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition-colors',
                  current ? 'border-transparent text-foreground' : 'text-muted-foreground hover:bg-muted',
                  !s.aiReachable && 'cursor-not-allowed opacity-70',
                )}
                style={current ? { background: `color-mix(in oklab, ${s.color} 22%, transparent)` } : undefined}
              >
                {!s.aiReachable ? <Lock className="size-3" /> : <span className="size-1.5 rounded-full" style={{ background: s.color }} />}
                {s.label}
              </button>
            </Tip>
          );
        })}
      </div>
      <Field label="Статус" className="max-w-xs">
        <OptionSelect<ClientStatus>
          value={card.status}
          disabled={change.isPending}
          onChange={(status) => status !== card.status && change.mutate({ status })}
          options={STATUSES.map((s) => ({ value: s, label: STATUS_LABELS[s] }))}
        />
      </Field>
    </Card>
  );
}

export function ClientPage() {
  const { id } = route.useParams();
  const clientId = positiveInt(id) ?? 0;
  const hash = useLocation({ select: (l) => l.hash });
  const conversation = useRef<HTMLElement>(null);
  const card = useQuery({ queryKey: qk.client(clientId), queryFn: () => clientsApi.get(clientId), enabled: clientId > 0 });

  useEffect(() => {
    if (card.data && hash === 'conversation') conversation.current?.scrollIntoView({ block: 'start' });
  }, [card.data, hash]);

  const notFound = clientId === 0 || (card.error instanceof ApiError && card.error.status === 404);

  return (
    <Page>
      <BackLink to="/clients">Все клиенты</BackLink>
      {card.data ? (
        <>
          <Card className="flex-row flex-wrap items-start gap-4">
            <Avatar name={card.data.display_name} url={card.data.avatar_url} size={64} />
            <div className="flex min-w-0 flex-1 flex-col gap-1">
              <h1 className="text-xl font-semibold sm:text-2xl">{card.data.display_name}</h1>
              <p className="text-sm text-muted-foreground">В базе с {formatDateTime(card.data.created_at)}</p>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => conversation.current?.scrollIntoView({ block: 'start', behavior: 'smooth' })}
                >
                  Перейти к переписке
                </Button>
                {card.data.channels.map((ch) => (
                  <Button key={ch.dialog_id} asChild variant="ghost" size="sm">
                    <Link to="/inbox" search={{ d: ch.dialog_id }}>
                      <ChannelIcon kind={ch.kind} size={18} />
                      Открыть диалог
                    </Link>
                  </Button>
                ))}
              </div>
            </div>
            <StatusBadge status={card.data.status} />
          </Card>

          <StageCard card={card.data} onChanged={() => void card.refetch()} />
          <ClientForm key={`${card.data.id}:${card.dataUpdatedAt}`} card={card.data} />

          <Card>
            <CardTitle id="hist-h">История</CardTitle>
            <Timeline card={card.data} />
          </Card>

          <section ref={conversation} id="conversation" aria-labelledby="conversation-h" className="flex scroll-mt-4 flex-col gap-3">
            <div>
              <h2 id="conversation-h" className="text-lg font-semibold">
                Лента общения
              </h2>
              <p className="text-sm text-muted-foreground">
                Все связанные каналы этого клиента. Ответить можно в конкретном диалоге.
              </p>
            </div>
            <ActivityFeed clientId={card.data.id} />
          </section>
        </>
      ) : notFound ? (
        <div role="alert">
          <EmptyState icon={UserX} title="Клиент не найден" hint="Возможно, запись удалена." />
        </div>
      ) : card.isError ? (
        <ErrorState title="Не удалось загрузить клиента" onRetry={() => void card.refetch()} />
      ) : (
        <EmptyState icon={Hourglass} title="Загружаем клиента…" />
      )}
    </Page>
  );
}

function Timeline({ card }: { card: ClientCard }) {
  const items = timelineWithDates(card);
  if (!items.length) return <EmptyState icon={History} title="Событий пока нет" className="py-6" />;
  return (
    <ol className="flex flex-col gap-3">
      {items.map((t, i) => {
        const Icon = TIMELINE_ICONS[t.icon];
        return (
          <li key={i} className="flex gap-3 text-sm">
            <span
              className={cn(
                'flex size-7 flex-none items-center justify-center rounded-full bg-muted text-muted-foreground',
                t.partner && 'bg-success/12 text-success',
              )}
            >
              <Icon className="size-3.5" />
            </span>
            <div className="min-w-0">
              <strong className="font-medium">{t.title}</strong>
              {t.detail && <span className="text-muted-foreground"> · {t.detail}</span>}
              <div className="text-xs text-muted-foreground">{t.when}</div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
