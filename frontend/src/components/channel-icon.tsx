import { Camera, MessageCircle, Radio, Send, type LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface ChannelLook {
  label: string;
  icon: LucideIcon;
  /** Text colour of the messenger (theme tokens `--telegram`, `--whatsapp`, `--instagram`). */
  text: string;
  /** A soft background in the same colour (badges, pressed filter chips). */
  soft: string;
}

/** Name, icon and colour of every messenger (docs/channels.md); unknown kinds get a neutral one. */
export const CHANNEL_LOOK: Record<string, ChannelLook> = {
  telegram_user: { label: 'Telegram', icon: Send, text: 'text-telegram', soft: 'bg-telegram/15' },
  instagram: { label: 'Instagram', icon: Camera, text: 'text-instagram', soft: 'bg-instagram/15' },
  whatsapp: { label: 'WhatsApp', icon: MessageCircle, text: 'text-whatsapp', soft: 'bg-whatsapp/15' },
};

const UNKNOWN: ChannelLook = { label: 'Канал', icon: Radio, text: 'text-muted-foreground', soft: 'bg-muted' };

export function channelLook(kind: string | null | undefined): ChannelLook {
  return (kind && CHANNEL_LOOK[kind]) || UNKNOWN;
}

/** The messenger of a chat or a channel, as a small labelled icon in its colour. */
export function ChannelIcon({ kind, className }: { kind: string | null | undefined; className?: string }) {
  const { label, icon: Icon, text } = channelLook(kind);
  return (
    <span role="img" aria-label={label} title={label} className="inline-flex flex-none items-center">
      <Icon aria-hidden="true" className={cn('size-3.5', text, className)} />
    </span>
  );
}

/**
 * The messenger as a round coloured badge: a table cell, the corner of a chat's avatar
 * (`ring` separates it from the avatar). `size` is the badge's side in px.
 */
export function ChannelBadge({ kind, size = 28, ring = false, className }: {
  kind: string | null | undefined;
  size?: number;
  ring?: boolean;
  className?: string;
}) {
  const { label, icon: Icon, text, soft } = channelLook(kind);
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      style={{ width: size, height: size }}
      className={cn(
        'inline-flex flex-none items-center justify-center rounded-full',
        ring ? 'bg-background ring-2 ring-background' : soft,
        text,
        className,
      )}
    >
      {ring ? (
        <span className={cn('flex size-full items-center justify-center rounded-full', soft)}>
          <Icon aria-hidden="true" style={{ width: size * 0.58, height: size * 0.58 }} />
        </span>
      ) : (
        <Icon aria-hidden="true" style={{ width: size * 0.5, height: size * 0.5 }} />
      )}
    </span>
  );
}
