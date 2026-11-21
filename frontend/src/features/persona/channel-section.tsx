import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, LogIn, Plus, PowerOff, Radio } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { ChannelIcon, channelLook } from '@/components/channel-icon';
import { useConfirm } from '@/components/confirm';
import { EmptyState, ErrorState } from '@/components/empty-state';
import { Section } from '@/components/field';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { formatDateTime } from '@/lib/format';
import { qk } from '@/lib/query';
import type { Channel, ChannelKind, ChannelKindInfo } from '@/lib/types';
import { channelApi } from './api';
import { CHANNEL_KINDS, availability, kindEntry } from './channel-kinds';
import { channelStatus, needsRelogin } from './channel-logic';
import { usePersonaId } from './persona-scope';

function ChannelRow({ channel, onRelogin }: { channel: Channel; onRelogin: (kind: ChannelKind) => void }) {
  const personaId = usePersonaId();
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const status = channelStatus(channel.status);
  const look = channelLook(channel.kind);
  const disable = useMutation({
    mutationFn: () => channelApi.disable(personaId, channel.id),
    onSuccess: () => {
      toast.success('Канал отключён — аккаунт выйдет из сети в течение минуты');
      void queryClient.invalidateQueries({ queryKey: qk.channels(personaId) });
      void queryClient.invalidateQueries({ queryKey: qk.personaList });
    },
  });
  const entry = kindEntry(channel.kind);
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <ChannelIcon kind={channel.kind} className="size-4" />
          <span className="font-medium">{look.label}</span>
          {channel.account_label && <span className="text-sm text-muted-foreground">{channel.account_label}</span>}
          <Badge variant={status.variant}>{status.label}</Badge>
        </div>
        {channel.last_activity_at && (
          <span className="text-xs text-muted-foreground">Последняя активность: {formatDateTime(channel.last_activity_at)}</span>
        )}
        {channel.last_error && <span className="text-xs break-words text-destructive">{channel.last_error}</span>}
      </div>
      <div className="flex flex-wrap gap-1">
        {entry && needsRelogin(channel.status) && (
          <Button variant="outline" size="sm" onClick={() => onRelogin(entry.kind)}>
            <LogIn />
            Войти заново
          </Button>
        )}
        {channel.status !== 'disabled' && (
          <Button
            variant="ghost-destructive"
            size="sm"
            disabled={disable.isPending}
            onClick={async () => {
              const ok = await confirm({
                title: 'Отключить канал?',
                message: 'Персонаж перестанет получать и отправлять сообщения через этот аккаунт. Вернуть — подключить заново.',
                confirmLabel: 'Отключить',
                danger: true,
              });
              if (ok) disable.mutate();
            }}
          >
            <PowerOff />
            Отключить
          </Button>
        )}
      </div>
    </li>
  );
}

/** «Подключить ▾»: the messengers this server can connect; a kind that can not says why. */
function ConnectMenu({ kinds, onPick }: { kinds: ChannelKindInfo[] | undefined; onPick: (kind: ChannelKind) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <Button variant="outline" size="sm" aria-expanded={open} aria-haspopup="true" onClick={() => setOpen(!open)}>
        <Plus />
        Подключить
        <ChevronDown />
      </Button>
      {open && (
        <div
          role="group"
          aria-label="Какой мессенджер подключить"
          className="absolute right-0 z-20 mt-1 flex w-72 flex-col gap-0.5 rounded-md border bg-popover p-1 shadow-md"
        >
          {CHANNEL_KINDS.map((kind) => {
            const { available, reason } = availability(kind.kind, kinds);
            const Icon = kind.icon;
            return (
              <button
                key={kind.kind}
                type="button"
                disabled={!available}
                className="flex items-start gap-2 rounded-sm px-2 py-1.5 text-left text-sm hover:bg-accent disabled:cursor-not-allowed disabled:opacity-70"
                onClick={() => {
                  setOpen(false);
                  onPick(kind.kind);
                }}
              >
                <Icon className="mt-0.5 size-4 flex-none" aria-hidden="true" />
                <span className="flex flex-col">
                  <span className="font-medium">{kind.label}</span>
                  <span className="text-xs text-muted-foreground">{available ? kind.hint : reason}</span>
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** «Канал»: the persona's messenger accounts, connecting one (Telegram, Instagram, WhatsApp), switching one off. */
export function ChannelSection({ pollMs }: { pollMs?: number }) {
  const personaId = usePersonaId();
  const channels = useQuery({ queryKey: qk.channels(personaId), queryFn: () => channelApi.list(personaId) });
  const [connecting, setConnecting] = useState<ChannelKind | null>(null);
  const items = channels.data?.items ?? [];
  const Connect = connecting ? kindEntry(connecting)?.Connect : undefined;
  return (
    <Section
      id="channel"
      title="Канал"
      description="Аккаунты Telegram, Instagram и WhatsApp, через которые говорит персонаж. Клиенты, написавшие на них, — её лиды."
      actions={!connecting && <ConnectMenu kinds={channels.data?.kinds} onPick={setConnecting} />}
    >
      {channels.isPending && <Skeleton className="h-12 w-full" />}
      {channels.isError && <ErrorState onRetry={() => void channels.refetch()} />}
      {channels.isSuccess && items.length === 0 && !connecting && (
        <EmptyState icon={Radio} title="Канал не подключён" hint="Без канала персонаж никому не отвечает." className="py-4" />
      )}
      {items.length > 0 && (
        <ul className="flex flex-col divide-y">
          {items.map((c) => <ChannelRow key={c.id} channel={c} onRelogin={setConnecting} />)}
        </ul>
      )}
      {Connect && <Connect key={connecting} onClose={() => setConnecting(null)} pollMs={pollMs} />}
    </Section>
  );
}
