import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { Merge, UserX, Users } from 'lucide-react';
import { toast } from 'sonner';
import { Avatar } from '@/components/avatar';
import { ChannelIcon } from '@/components/channel-icon';
import { useConfirm } from '@/components/confirm';
import { Section } from '@/components/field';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { qk } from '@/lib/query';
import type { ClientMatch, MatchClient, MatchReason } from '@/lib/types';
import { clientName } from '@/lib/utils';
import { leadsApi } from './api';

/** The messengers a client writes from, as icons (one account per messenger). */
export function MessengerIcons({ kinds }: { kinds: readonly string[] }) {
  return (
    <span className="inline-flex items-center gap-1">
      {kinds.map((kind) => <ChannelIcon key={kind} kind={kind} />)}
    </span>
  );
}

/** After a merge or «разные люди»: the leads, the cards, the suggestions, the chats and their journals. */
function useInvalidateClients(): () => void {
  const queryClient = useQueryClient();
  return () => {
    for (const queryKey of [qk.clients, qk.chats, qk.timelines]) void queryClient.invalidateQueries({ queryKey });
  };
}

/**
 * Merge `other` into `keep` after a confirmation that says what moves and that it can not be undone.
 * Resolves true when merged.
 */
export function useMergeClients(): { merge: (keep: MatchClient, other: MatchClient) => Promise<boolean>; pending: boolean } {
  const confirm = useConfirm();
  const invalidate = useInvalidateClients();
  const mutation = useMutation({
    mutationFn: ({ keep, other }: { keep: MatchClient; other: MatchClient }) => leadsApi.merge(keep.id, other.id),
    onSuccess: (_detail, { keep, other }) => {
      invalidate();
      toast.success(`«${clientName(other)}» объединён с «${clientName(keep)}»`);
    },
  });
  const merge = async (keep: MatchClient, other: MatchClient): Promise<boolean> => {
    const ok = await confirm({
      title: `Объединить «${clientName(other)}» с «${clientName(keep)}»?`,
      message:
        `Чаты, переписка, память и паспорт «${clientName(other)}» перейдут в карточку «${clientName(keep)}», ` +
        'Марина будет видеть его переписку во всех мессенджерах. ИИ — как у карточки, которая остаётся. ' +
        'Разделить обратно нельзя.',
      confirmLabel: 'Объединить',
      danger: true,
    });
    if (!ok) return false;
    try {
      await mutation.mutateAsync({ keep, other });
      return true;
    } catch {
      return false;     // the error toast comes from the query client (lib/query.ts)
    }
  };
  return { merge, pending: mutation.isPending };
}

function useMarkDistinct() {
  const invalidate = useInvalidateClients();
  return useMutation({
    mutationFn: ({ a, b }: { a: MatchClient; b: MatchClient }) => leadsApi.distinct(a.id, b.id),
    onSuccess: () => {
      invalidate();
      toast.success('Больше не предлагаем эту пару');
    },
  });
}

function Reasons({ reasons }: { reasons: readonly MatchReason[] }) {
  return (
    <ul aria-label="Почему" className="flex flex-wrap gap-1.5">
      {reasons.map((r) => (
        <li key={r.text}>
          <Badge variant={r.kind === 'name' ? 'muted' : 'secondary'}>{r.text}</Badge>
        </li>
      ))}
    </ul>
  );
}

function Who({ client }: { client: MatchClient }) {
  const name = clientName(client);
  return (
    <span className="flex min-w-0 items-center gap-2.5">
      <Avatar name={name} size={32} />
      <span className="flex min-w-0 flex-col">
        <Link to="/leads/$clientId" params={{ clientId: String(client.id) }} className="truncate font-medium hover:underline">
          {name}
        </Link>
        <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <MessengerIcons kinds={client.messengers} />
          {[client.username && `@${client.username}`, client.persona?.name].filter(Boolean).join(' · ')}
        </span>
      </span>
    </span>
  );
}

/**
 * One suggested pair: who, why, «Объединить» (`other` moves into `keep`) and «Разные люди».
 * `showKeep` — the leads page shows both sides; the lead card shows only the other one.
 */
function MatchRow({ keep, other, reasons, showKeep }: {
  keep: MatchClient;
  other: MatchClient;
  reasons: readonly MatchReason[];
  showKeep: boolean;
}) {
  const { merge, pending } = useMergeClients();
  const distinct = useMarkDistinct();
  return (
    <li className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0 md:flex-row md:items-center md:gap-4">
      <span className="flex min-w-0 flex-1 flex-col gap-2 md:flex-row md:items-center md:gap-4">
        {showKeep && <Who client={keep} />}
        {showKeep && <span aria-hidden className="hidden text-muted-foreground md:inline">↔</span>}
        <Who client={other} />
        <Reasons reasons={reasons} />
      </span>
      <span className="flex flex-none gap-2">
        <Button size="sm" disabled={pending} onClick={() => void merge(keep, other)}>
          <Merge />
          Объединить
        </Button>
        <Button size="sm" variant="outline" disabled={distinct.isPending} onClick={() => distinct.mutate({ a: keep, b: other })}>
          <UserX />
          Разные люди
        </Button>
      </span>
    </li>
  );
}

const HINT =
  'Совпали телефон, username, полное имя или в переписке назван контакт другого. Сами клиенты не объединяются: решает оператор.';

/** In the lead card: the clients that may be this same person (nothing when there are none). */
export function ClientMatches({ client }: { client: MatchClient }) {
  const matches = useQuery({ queryKey: qk.clientMatches(client.id), queryFn: () => leadsApi.matches(client.id) });
  const items = matches.data?.items ?? [];
  if (items.length === 0) return null;
  const otherOf = (m: ClientMatch): MatchClient => (m.clients[0].id === client.id ? m.clients[1] : m.clients[0]);
  return (
    <Section id="matches" title="Возможно, это один человек" description={`${HINT} «Объединить» оставит эту карточку.`}>
      <ul className="flex flex-col divide-y">
        {items.map((m) => (
          <MatchRow key={otherOf(m).id} keep={client} other={otherOf(m)} reasons={m.reasons} showKeep={false} />
        ))}
      </ul>
    </Section>
  );
}

/** On the leads page: every pair that may be one person; «Объединить» keeps the left (older) client. */
export function MatchesPanel() {
  const matches = useQuery({ queryKey: qk.clientMatches(), queryFn: () => leadsApi.matches() });
  const items = matches.data?.items ?? [];
  if (items.length === 0) return null;
  return (
    <section aria-labelledby="all-matches-title" className="flex flex-col gap-3 rounded-xl border border-warning/40 bg-warning/5 p-4">
      <header className="flex flex-col gap-1">
        <h2 id="all-matches-title" className="flex items-center gap-2 font-semibold">
          <Users className="size-4" />
          Возможно, один человек: {items.length}
        </h2>
        <p className="text-sm text-muted-foreground">{HINT} «Объединить» оставит левую карточку.</p>
      </header>
      <ul className="flex flex-col divide-y">
        {items.map((m) => (
          <MatchRow key={`${m.clients[0].id}-${m.clients[1].id}`} keep={m.clients[0]} other={m.clients[1]} reasons={m.reasons} showKeep />
        ))}
      </ul>
    </section>
  );
}
