import { AlertTriangle } from 'lucide-react';
import { Avatar } from '@/components/avatar';
import type { ChatRow } from '@/lib/types';
import { clientName, cn } from '@/lib/utils';
import { aiActive } from './timeline-logic';

/** The pieces a chat row shows in the narrow list and in the wide table alike. */

/** The client's avatar with the AI dot: green — she answers here, grey — the AI is off. */
export function ChatAvatar({ chat, size = 42 }: { chat: ChatRow; size?: number }) {
  const ai = aiActive(chat);
  return (
    <span className="relative flex-none">
      <Avatar name={clientName(chat.client)} size={size} />
      <span
        className={cn(
          'absolute right-0 bottom-0 size-3 rounded-full border-2 border-background',
          ai ? 'bg-success' : 'bg-muted-foreground/50',
        )}
        role="img"
        aria-label={ai ? 'ИИ отвечает' : 'ИИ выключен'}
        title={ai ? 'ИИ отвечает' : 'ИИ выключен'}
      />
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
