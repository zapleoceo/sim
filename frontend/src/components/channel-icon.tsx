import { Camera, MessageCircle, Radio, Send, type LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';

/** Name and icon of every messenger (docs/channels.md); unknown kinds get a neutral one. */
export const CHANNEL_LOOK: Record<string, { label: string; icon: LucideIcon }> = {
  telegram_user: { label: 'Telegram', icon: Send },
  instagram: { label: 'Instagram', icon: Camera },
  whatsapp: { label: 'WhatsApp', icon: MessageCircle },
};

export function channelLook(kind: string | null | undefined): { label: string; icon: LucideIcon } {
  return (kind && CHANNEL_LOOK[kind]) || { label: 'Канал', icon: Radio };
}

/** The messenger of a chat or a channel, as a small labelled icon. */
export function ChannelIcon({ kind, className }: { kind: string | null | undefined; className?: string }) {
  const { label, icon: Icon } = channelLook(kind);
  return (
    <span role="img" aria-label={label} title={label} className="inline-flex flex-none items-center">
      <Icon aria-hidden="true" className={cn('size-3.5 text-muted-foreground', className)} />
    </span>
  );
}
