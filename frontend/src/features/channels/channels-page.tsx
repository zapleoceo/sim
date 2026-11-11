import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { Cable, Plus } from 'lucide-react';
import { useState } from 'react';
import { ChannelIcon } from '@/components/badges';
import { useConfirm } from '@/components/confirm';
import { EmptyState, ErrorState } from '@/components/empty-state';
import { Page, PageHeader } from '@/components/page';
import { Badge } from '@/components/ui/badge';
import { Button, buttonVariants } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { channelsApi } from '@/lib/api/channels';
import { personasApi } from '@/lib/api/personas';
import { humanizeChannelError } from '@/lib/channel-error';
import { formatDateTime } from '@/lib/format';
import type { Channel, Persona } from '@/lib/models';
import { qk } from '@/lib/query';
import { CHANNEL_LABELS } from '@/lib/stages';
import { cn } from '@/lib/utils';
import { STATUS_VIEW } from './channel-wizard-logic';
import { ChannelWizard } from './channel-wizard';

export interface ChannelVm {
  channel: Channel;
  label: string;
  tone: (typeof STATUS_VIEW)[keyof typeof STATUS_VIEW]['tone'];
  persona: string;
  activity: string;
  kindLabel: string;
  error: string;
}

export function channelVm(channel: Channel, personas: Persona[]): ChannelVm {
  const view = STATUS_VIEW[channel.status] ?? { label: channel.status, tone: 'muted' as const };
  return {
    channel,
    ...view,
    persona: personas.find((p) => p.id === channel.persona_id)?.name ?? '—',
    activity: formatDateTime(channel.last_activity_at),
    kindLabel: CHANNEL_LABELS[channel.kind] ?? channel.kind,
    error: humanizeChannelError(channel.last_error),
  };
}

export function ChannelsPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const [wizard, setWizard] = useState<{ channel?: Channel } | null>(null);

  const channels = useQuery({ queryKey: qk.channels, queryFn: channelsApi.list });
  const personas = useQuery({ queryKey: qk.personas, queryFn: personasApi.list });
  const personaList = personas.data ?? [];
  const loaded = channels.isSuccess && personas.isSuccess;

  const remove = useMutation({
    mutationFn: (c: Channel) => channelsApi.remove(c.id),
    onSuccess: (_r, c) => queryClient.setQueryData<Channel[]>(qk.channels, (list) => list?.filter((x) => x.id !== c.id)),
  });

  const disconnect = async (c: Channel): Promise<void> => {
    const ok = await confirm({
      title: 'Отключить канал?',
      message: `«${c.name}» перестанет принимать и отправлять сообщения, сессия будет отвязана.`,
      confirmLabel: 'Отключить',
      danger: true,
    });
    if (ok) remove.mutate(c);
  };

  const closeWizard = (changed: boolean): void => {
    setWizard(null);
    if (changed) void queryClient.invalidateQueries({ queryKey: qk.channels });
  };

  return (
    <Page>
      <PageHeader title="Каналы" subtitle="Telegram, WhatsApp и Instagram, через которые персонажи общаются с клиентами">
        <Button onClick={() => setWizard({})} disabled={!personaList.length}>
          <Plus />
          Добавить канал
        </Button>
      </PageHeader>

      {loaded && !personaList.length && (
        <p className="rounded-xl border bg-card p-4 text-sm text-muted-foreground">
          Сначала создайте хотя бы одного персонажа — канал привязывается к нему.
        </p>
      )}

      {channels.isError ? (
        <ErrorState onRetry={() => void channels.refetch()} />
      ) : channels.isPending ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="h-44 rounded-xl" />
          ))}
        </div>
      ) : channels.data.length === 0 ? (
        <EmptyState icon={Cable} title="Каналов пока нет" hint="Добавьте Telegram-бота, номер WhatsApp или аккаунт Instagram." />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {channels.data.map((c) => {
            const vm = channelVm(c, personaList);
            return (
              <article key={c.id} aria-label={c.name} className="flex flex-col gap-3 rounded-xl border bg-card p-4">
                <header className="flex items-center gap-3">
                  <ChannelIcon kind={c.kind} size={32} />
                  <div className="min-w-0 flex-1">
                    <h3 className="truncate font-medium">{vm.kindLabel}</h3>
                    <p className="truncate text-sm text-muted-foreground">{vm.persona}</p>
                  </div>
                  <Badge variant={vm.tone}>{vm.label}</Badge>
                </header>
                <dl className="flex flex-col gap-1 text-sm">
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">Название</dt>
                    <dd className="truncate">{c.name}</dd>
                  </div>
                  {c.account_label && (
                    <div className="flex justify-between gap-3">
                      <dt className="text-muted-foreground">Аккаунт</dt>
                      <dd className="truncate">{c.account_label}</dd>
                    </div>
                  )}
                  {vm.activity && (
                    <div className="flex justify-between gap-3">
                      <dt className="text-muted-foreground">Активность</dt>
                      <dd>{vm.activity}</dd>
                    </div>
                  )}
                </dl>
                {vm.error && (
                  <p role="alert" className="text-sm text-destructive [overflow-wrap:anywhere]">
                    {vm.error}
                  </p>
                )}
                <footer className="mt-auto flex flex-wrap items-center gap-2">
                  <Link to="/activity" search={{ channel_id: c.id }} className={cn(buttonVariants({ variant: 'ghost', size: 'sm' }))}>
                    Активность канала
                  </Link>
                  {c.status !== 'active' && (
                    <Button variant="outline" size="sm" onClick={() => setWizard({ channel: c })}>
                      Подключить
                    </Button>
                  )}
                  <Button variant="ghost-destructive" size="sm" className="ml-auto" onClick={() => void disconnect(c)}>
                    Отключить
                  </Button>
                </footer>
              </article>
            );
          })}
        </div>
      )}

      {wizard && <ChannelWizard personas={personaList} channel={wizard.channel} onClose={closeWizard} />}
    </Page>
  );
}
