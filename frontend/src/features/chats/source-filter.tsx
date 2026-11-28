import { channelLook } from '@/components/channel-icon';
import type { ChannelKind, ChatSource } from '@/lib/types';
import { cn } from '@/lib/utils';
import { SOURCE_KINDS, toggleSource } from './sources-logic';

const chip =
  'inline-flex h-8 flex-none items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none';
const on = 'border-primary bg-primary text-primary-foreground';
const off = 'bg-background text-muted-foreground hover:bg-accent hover:text-foreground';

/**
 * «Источник» of the chat list: «Все» plus one chip per messenger, several may be pressed. Each
 * chip shows how many chats (and unread messages) that messenger has; a messenger without chats
 * is still offered, so a link with it keeps working.
 */
export function SourceFilter({ value, onChange, counts = [], className }: {
  value: readonly ChannelKind[];
  onChange: (kinds: ChannelKind[]) => void;
  counts?: readonly ChatSource[];
  className?: string;
}) {
  const countOf = (kind: ChannelKind) => counts.find((c) => c.kind === kind);
  return (
    <div role="group" aria-label="Источник" className={cn('flex flex-wrap gap-1.5', className)}>
      <button type="button" aria-pressed={value.length === 0} className={cn(chip, value.length === 0 ? on : off)} onClick={() => onChange([])}>
        Все
      </button>
      {SOURCE_KINDS.map((kind) => {
        const { label, icon: Icon } = channelLook(kind);
        const pressed = value.includes(kind);
        const count = countOf(kind);
        return (
          <button
            key={kind}
            type="button"
            aria-pressed={pressed}
            className={cn(chip, pressed ? on : off)}
            onClick={() => onChange(toggleSource(value, kind))}
          >
            <Icon aria-hidden="true" className="size-3.5" />
            {label}
            {count && (
              <span className="tabular opacity-80" aria-label={`чатов: ${count.chats}${count.unread ? `, непрочитанных: ${count.unread}` : ''}`}>
                {count.chats}
                {count.unread > 0 && <span className="ml-1 font-semibold">· {count.unread > 99 ? '99+' : count.unread}</span>}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
