import { channelLook } from '@/components/channel-icon';
import type { ChatChannel } from '@/lib/types';
import { cn } from '@/lib/utils';

/** Did the messenger change between an older event and this one (a feed over several chats)? */
export function messengerChanged(item: { channel?: ChatChannel | null }, older: { channel?: ChatChannel | null } | undefined): boolean {
  return !older || older.channel?.kind !== item.channel?.kind;
}

/**
 * «→ WhatsApp»: from here on the feed of a person goes on in another messenger (the conversation
 * of /chats and «Лента» of the lead card).
 */
export function MessengerChange({ kind, className }: { kind: string | null | undefined; className?: string }) {
  const { label, icon: Icon, text, soft } = channelLook(kind);
  return (
    <div className={cn('flex items-center gap-2 py-1 text-xs', className)} role="separator" aria-label={`Дальше — ${label}`}>
      <span aria-hidden className="h-px flex-1 bg-border" />
      <span className={cn('inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 font-medium', soft)}>
        <span aria-hidden>→</span>
        <Icon aria-hidden="true" className={cn('size-3.5', text)} />
        <span>{label}</span>
      </span>
      <span aria-hidden className="h-px flex-1 bg-border" />
    </div>
  );
}
