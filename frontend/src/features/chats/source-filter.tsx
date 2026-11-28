import { channelLook } from '@/components/channel-icon';
import type { ChannelKind, ChatSource } from '@/lib/types';
import { cn } from '@/lib/utils';
import { SOURCE_KINDS, toggleSource } from './sources-logic';

const chip =
  'inline-flex h-8 flex-none items-center gap-1.5 rounded-full border px-3 text-xs font-medium whitespace-nowrap transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none';
const off = 'bg-background text-muted-foreground hover:bg-accent hover:text-foreground';

/**
 * «Источник» of the chat list: «Все» plus one chip per messenger in its colour, several may be
 * pressed. A chip shows how many chats that messenger has and, in a blue dot, how many unread
 * messages; a messenger without chats is still offered, so a link with it keeps working. On a
 * phone the chips stay in one row that scrolls sideways.
 */
export function SourceFilter({ value, onChange, counts = [], className }: {
  value: readonly ChannelKind[];
  onChange: (kinds: ChannelKind[]) => void;
  counts?: readonly ChatSource[];
  className?: string;
}) {
  const all = value.length === 0;
  return (
    <div
      role="group"
      aria-label="Источник"
      className={cn('-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-0.5 scrollbar-none', className)}
    >
      <button
        type="button"
        aria-pressed={all}
        className={cn(chip, all ? 'border-primary bg-primary text-primary-foreground' : off)}
        onClick={() => onChange([])}
      >
        Все
      </button>
      {SOURCE_KINDS.map((kind) => {
        const { label, icon: Icon, text, soft } = channelLook(kind);
        const pressed = value.includes(kind);
        const count = counts.find((c) => c.kind === kind);
        return (
          <button
            key={kind}
            type="button"
            aria-pressed={pressed}
            className={cn(chip, pressed ? cn('border-transparent font-semibold text-foreground', soft) : off)}
            onClick={() => onChange(toggleSource(value, kind))}
          >
            <Icon aria-hidden="true" className={cn('size-3.5', text)} />
            {label}
            {count && (
              <span className="tabular text-muted-foreground" aria-label={`чатов: ${count.chats}`}>
                {count.chats}
              </span>
            )}
            {count && count.unread > 0 && (
              <span
                className="flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-semibold text-primary-foreground tabular"
                aria-label={`непрочитанных: ${count.unread}`}
              >
                {count.unread > 99 ? '99+' : count.unread}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
