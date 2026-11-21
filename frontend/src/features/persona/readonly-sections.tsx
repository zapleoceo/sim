import { useQuery } from '@tanstack/react-query';
import { Radio } from 'lucide-react';
import { EmptyState, ErrorState } from '@/components/empty-state';
import { Section } from '@/components/field';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { formatDateTime } from '@/lib/format';
import { qk } from '@/lib/query';
import type { Channel } from '@/lib/types';
import { personaApi } from './api';

/** The JSON-plan format rules: a constant in the backend code, shown read-only. */
export function FormatRulesSection() {
  const rules = useQuery({ queryKey: qk.instructions, queryFn: personaApi.instructions, staleTime: Infinity });
  return (
    <Section id="format" title="Правила формата" description="Как модель отвечает планом. Задаются в коде, здесь только для чтения.">
      {rules.isPending && <Skeleton className="h-40 w-full" />}
      {rules.isError && <ErrorState onRetry={() => void rules.refetch()} />}
      {rules.data && (
        <pre className="max-h-96 overflow-auto rounded-md bg-muted/60 p-3 font-mono text-xs leading-relaxed break-words whitespace-pre-wrap">
          {rules.data.text}
        </pre>
      )}
    </Section>
  );
}

const CHANNEL_STATUS: Record<string, { label: string; variant: 'success' | 'destructive' | 'muted' | 'warning' }> = {
  connected: { label: 'подключён', variant: 'success' },
  error: { label: 'ошибка', variant: 'destructive' },
  new: { label: 'не подключён', variant: 'warning' },
  disabled: { label: 'выключен', variant: 'muted' },
};

function ChannelRow({ channel }: { channel: Channel }) {
  const status = CHANNEL_STATUS[channel.status] ?? { label: channel.status, variant: 'muted' as const };
  return (
    <li className="flex flex-col gap-1 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{channel.title || 'Telegram'}</span>
        {channel.account_label && <span className="text-sm text-muted-foreground">{channel.account_label}</span>}
        <Badge variant={status.variant}>{status.label}</Badge>
      </div>
      {channel.last_activity_at && (
        <span className="text-xs text-muted-foreground">Последняя активность: {formatDateTime(channel.last_activity_at)}</span>
      )}
      {channel.last_error && <span className="text-xs break-words text-destructive">{channel.last_error}</span>}
    </li>
  );
}

/** Messenger accounts the persona speaks through (status only; connecting is a server task). */
export function ChannelSection() {
  const channels = useQuery({ queryKey: qk.channels, queryFn: personaApi.channels });
  return (
    <Section id="channel" title="Канал">
      {channels.isPending && <Skeleton className="h-12 w-full" />}
      {channels.isError && <ErrorState onRetry={() => void channels.refetch()} />}
      {channels.data && channels.data.items.length === 0 && (
        <EmptyState icon={Radio} title="Канал не подключён" className="py-4" />
      )}
      {channels.data && channels.data.items.length > 0 && (
        <ul className="flex flex-col divide-y">
          {channels.data.items.map((c) => <ChannelRow key={c.id} channel={c} />)}
        </ul>
      )}
    </Section>
  );
}
