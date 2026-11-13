import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getRouteApi, Link } from '@tanstack/react-router';
import { ChevronDown, Hourglass, Pencil, UserX } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { toast } from 'sonner';
import { Avatar } from '@/components/avatar';
import { ChannelIcon } from '@/components/badges';
import { EmptyState, ErrorState } from '@/components/empty-state';
import { OptionSelect } from '@/components/option-select';
import { BackLink, Page } from '@/components/page';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { clientsApi } from '@/lib/api/clients';
import { ApiError } from '@/lib/api/http';
import { formatDateTime, formatShort, isoToLocalInput, localInputToIso } from '@/lib/format';
import { useLiveDialogs } from '@/lib/live';
import type { ClientCard, ClientPatch, ClientStatus, Stage } from '@/lib/models';
import { qk } from '@/lib/query';
import { PARTNER_ONLY_HINT, STAGES, STATUSES, STATUS_LABELS, stageMeta } from '@/lib/stages';
import { positiveInt } from '@/lib/utils';
import { ClientChat } from './client-chat';
import { timelineWithDates } from './client-logic';

const route = getRouteApi('/_app/clients/$id');

/** Saves one field of the card at once (no form, no «Сохранить»): the card stays calm. */
function useClientPatch(card: ClientCard) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (patch: ClientPatch) => clientsApi.patch(card.id, patch),
    onSuccess: (updated) => {
      queryClient.setQueryData<ClientCard>(qk.client(card.id), (prev) => (prev ? { ...prev, ...updated } : prev));
      toast.success('Сохранено', { duration: 1200 });
    },
  });
}

/** A text that saves itself when the field loses focus (Enter in a one-line field). */
function AutoSaveText({ label, value, onSave, multiline = false, placeholder }: {
  label: string;
  value: string;
  onSave: (v: string) => void;
  multiline?: boolean;
  placeholder?: string;
}) {
  const [draft, setDraft] = useState(value);
  const commit = (): void => {
    if (draft.trim() !== value.trim()) onSave(draft.trim());
  };
  const common = {
    'aria-label': label,
    value: draft,
    placeholder,
    onBlur: commit,
    className: 'border-transparent bg-transparent shadow-none px-0 hover:bg-muted/40 focus-visible:bg-background focus-visible:px-2',
  };
  return multiline ? (
    <Textarea {...common} rows={3} onChange={(e) => setDraft(e.target.value)} className={`${common.className} min-h-16 resize-y`} />
  ) : (
    <Input {...common} onChange={(e) => setDraft(e.target.value)}
      onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); }} />
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-1.5 border-t pt-4 first:border-t-0 first:pt-0">
      <h2 className="text-xs font-medium text-muted-foreground">{title}</h2>
      {children}
    </section>
  );
}

/** Folded by default: opened only when needed. */
function Fold({ title, children, open = false }: { title: string; children: ReactNode; open?: boolean }) {
  return (
    <details open={open} className="group border-t pt-4">
      <summary className="flex cursor-pointer list-none items-center justify-between text-xs font-medium text-muted-foreground">
        {title}
        <ChevronDown className="size-4 transition-transform group-open:rotate-180" />
      </summary>
      <div className="pt-2 text-sm">{children}</div>
    </details>
  );
}

function Header({ card }: { card: ClientCard }) {
  const patch = useClientPatch(card);
  const [renaming, setRenaming] = useState(false);
  const meta = stageMeta(card.stage);
  const last = card.last_contact_at ? `последний контакт ${formatShort(card.last_contact_at)}` : 'ещё не общались';
  return (
    <header className="flex flex-wrap items-center gap-4">
      <Avatar name={card.display_name} url={card.avatar_url} size={52} />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        {renaming ? (
          <AutoSaveText label="Имя" value={card.display_name}
            onSave={(v) => { if (v) patch.mutate({ display_name: v }); setRenaming(false); }} />
        ) : (
          <h1 className="group flex items-center gap-2 text-xl font-semibold">
            <span className="truncate">{card.display_name}</span>
            <Button variant="ghost" size="icon-sm" aria-label="Переименовать" onClick={() => setRenaming(true)}
              className="opacity-0 group-hover:opacity-100 focus-visible:opacity-100">
              <Pencil />
            </Button>
          </h1>
        )}
        <p className="flex flex-wrap items-center gap-x-2 text-sm text-muted-foreground">
          <span className="inline-flex items-center gap-1.5">
            <span className="size-1.5 rounded-full" style={{ background: meta.color }} />
            {meta.label}
          </span>
          <span>·</span>
          <span>{STATUS_LABELS[card.status]}</span>
          <span>·</span>
          <span>{last}</span>
        </p>
      </div>
      {card.channels.map((ch) => (
        <Button key={ch.dialog_id} asChild variant="outline" size="sm">
          <Link to="/inbox" search={{ d: ch.dialog_id }}>
            <ChannelIcon kind={ch.kind} size={16} />
            Открыть диалог
          </Link>
        </Button>
      ))}
    </header>
  );
}

function SidePanel({ card, onChanged }: { card: ClientCard; onChanged: () => void }) {
  const patch = useClientPatch(card);
  const change = useMutation({
    mutationFn: (body: { stage?: Stage; status?: ClientStatus }) => clientsApi.changeStage(card.id, body),
    onSuccess: onChanged,
  });
  const history = timelineWithDates(card);
  const passport = (card.passport ?? '').trim();

  return (
    <aside aria-label="О клиенте" className="flex flex-col gap-4 rounded-xl border bg-card p-4 lg:sticky lg:top-4">
      <Section title="Этап и статус">
        <div className="grid grid-cols-2 gap-2">
          <OptionSelect<Stage>
            aria-label="Этап"
            value={card.stage}
            disabled={change.isPending}
            onChange={(stage) => stage !== card.stage && change.mutate({ stage })}
            options={STAGES.map((s) => ({ value: s.id, label: s.label, disabled: !s.aiReachable }))}
          />
          <OptionSelect<ClientStatus>
            aria-label="Статус"
            value={card.status}
            disabled={change.isPending}
            onChange={(status) => status !== card.status && change.mutate({ status })}
            options={STATUSES.map((s) => ({ value: s, label: STATUS_LABELS[s] }))}
          />
        </div>
        <p className="text-xs text-muted-foreground">Регистрация и оплата {PARTNER_ONLY_HINT}.</p>
      </Section>

      <Section title="Следующий шаг">
        <AutoSaveText key={`ns:${card.next_step ?? ''}`} label="Следующий шаг" value={card.next_step ?? ''}
          placeholder="Например: напомнить про ссылку" onSave={(v) => patch.mutate({ next_step: v })} />
        <Input type="datetime-local" aria-label="Когда" defaultValue={isoToLocalInput(card.next_step_at)}
          key={`at:${card.next_step_at ?? ''}`}
          className="h-8 w-fit border-transparent bg-transparent px-0 text-xs text-muted-foreground shadow-none hover:bg-muted/40"
          onBlur={(e) => {
            const iso = localInputToIso(e.target.value);
            if (iso !== (card.next_step_at ?? null)) patch.mutate({ next_step_at: iso });
          }} />
      </Section>

      {card.needs.length > 0 && (
        <Section title="Потребности">
          <span className="flex flex-wrap gap-1.5">
            {card.needs.map((n) => (
              <span key={n} className="rounded-md bg-muted px-2 py-0.5 text-xs">{n}</span>
            ))}
          </span>
        </Section>
      )}

      <Section title="Заметки">
        <AutoSaveText key={`notes:${card.notes ?? ''}`} label="Заметки" value={card.notes ?? ''} multiline
          placeholder="Пусто" onSave={(v) => patch.mutate({ notes: v })} />
      </Section>

      <Fold title="Паспорт собеседника">
        {passport ? (
          <>
            <p className="whitespace-pre-wrap">{passport}</p>
            {card.passport_updated_at && (
              <p className="pt-2 text-xs text-muted-foreground">Обновлён {formatDateTime(card.passport_updated_at)}</p>
            )}
          </>
        ) : (
          <p className="text-muted-foreground">Пока пусто: паспортистка заполнит его после первых ответов.</p>
        )}
      </Fold>

      <Fold title={`История${history.length ? ` · ${history.length}` : ''}`}>
        {history.length ? (
          <ol className="flex flex-col gap-2">
            {history.map((t, i) => (
              <li key={i} className="flex flex-col">
                <span className={t.partner ? 'text-success' : undefined}>{t.title}</span>
                <span className="text-xs text-muted-foreground">{t.when}{t.detail ? ` · ${t.detail}` : ''}</span>
              </li>
            ))}
          </ol>
        ) : (
          <p className="text-muted-foreground">Событий пока нет.</p>
        )}
      </Fold>

      <p className="border-t pt-3 text-xs text-muted-foreground">В базе с {formatDateTime(card.created_at)}</p>
    </aside>
  );
}

/**
 * Client card: the conversation first (a calm chat), everything else in one quiet side panel.
 * Fields save themselves; history and passport are folded. Live events refresh it.
 */
export function ClientPage() {
  const { id } = route.useParams();
  const clientId = positiveInt(id) ?? 0;
  useLiveDialogs(clientId > 0);
  const card = useQuery({ queryKey: qk.client(clientId), queryFn: () => clientsApi.get(clientId), enabled: clientId > 0 });
  const notFound = clientId === 0 || (card.error instanceof ApiError && card.error.status === 404);

  return (
    <Page>
      <BackLink to="/clients">Все клиенты</BackLink>
      {card.data ? (
        <>
          <Header card={card.data} />
          <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
            <section aria-label="Переписка" className="min-w-0 rounded-xl bg-muted/30 p-3 sm:p-5">
              <ClientChat clientId={card.data.id} />
            </section>
            <SidePanel card={card.data} onChanged={() => void card.refetch()} />
          </div>
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
