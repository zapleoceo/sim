import { AlertTriangle } from 'lucide-react';
import { Avatar } from '@/components/avatar';
import { ChannelBadge } from '@/components/channel-icon';
import type { ChatRow } from '@/lib/types';
import { clientName, cn } from '@/lib/utils';
import { aiActive } from './timeline-logic';

/** The pieces a chat row shows in the narrow list and in the wide table alike. */

/** The client's avatar with the messenger of the chat in its corner. */
export function ChatAvatar({ chat, size = 44, messenger = true }: { chat: ChatRow; size?: number; messenger?: boolean }) {
  return (
    <span className="relative flex-none">
      <Avatar name={clientName(chat.client)} size={size} />
      {messenger && chat.channel && (
        <ChannelBadge kind={chat.channel.kind} size={Math.round(size * 0.42)} ring className="absolute -right-1 -bottom-1" />
      )}
    </span>
  );
}

/** «отвечает» / «ИИ выключен»: whether she answers here (the chat's and the client's switches). */
export function AiState({ chat }: { chat: ChatRow }) {
  const on = aiActive(chat);
  return (
    <span className={cn('inline-flex items-center gap-1.5 text-xs whitespace-nowrap', on ? 'text-foreground' : 'text-muted-foreground')}>
      <span aria-hidden className={cn('size-2 rounded-full', on ? 'bg-success' : 'bg-muted-foreground/50')} />
      {on ? 'отвечает' : 'ИИ выключен'}
    </span>
  );
}

export function UnreadBadge({ count }: { count: number }) {
  if (count <= 0) return null;
  return (
    <span
      className="flex h-5 min-w-5 flex-none items-center justify-center rounded-full bg-primary px-1.5 text-[11px] font-semibold text-primary-foreground tabular"
      aria-label={`Непрочитанных: ${count}`}
    >
      {count > 99 ? '99+' : count}
    </span>
  );
}

export function OperatorBadge() {
  return (
    <span className="inline-flex flex-none items-center gap-1 rounded-md bg-warning/15 px-1.5 py-0.5 text-[11px] font-medium whitespace-nowrap text-warning">
      <AlertTriangle className="size-3" />
      нужен оператор
    </span>
  );
}
