import { Camera, MessageCircle, Send, User, type LucideIcon } from 'lucide-react';
import type { ChannelKind, ClientStatus } from '@/lib/models';
import { CHANNEL_LABELS, STATUS_LABELS, stageMeta } from '@/lib/stages';
import { cn } from '@/lib/utils';
import { Badge } from './ui/badge';

const CHANNEL_STYLE: Record<ChannelKind, { icon: LucideIcon; className: string }> = {
  telegram: { icon: Send, className: 'bg-[#2a8fd1]' },
  telegram_user: { icon: User, className: 'bg-[#1c6fa8]' },
  whatsapp: { icon: MessageCircle, className: 'bg-[#25a35a]' },
  instagram: { icon: Camera, className: 'bg-gradient-to-br from-[#a255c9] via-[#e0527a] to-[#f0963c]' },
};

export function ChannelIcon({ kind, size = 22 }: { kind: ChannelKind; size?: number }) {
  const s = CHANNEL_STYLE[kind] ?? CHANNEL_STYLE.telegram;
  const Icon = s.icon;
  const label = CHANNEL_LABELS[kind] ?? kind;
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      className={cn('inline-flex flex-none items-center justify-center rounded-md text-white', s.className)}
      style={{ width: size, height: size }}
    >
      <Icon style={{ width: size * 0.6, height: size * 0.6 }} />
    </span>
  );
}

export function StageChip({ stage, className }: { stage: string; className?: string }) {
  const meta = stageMeta(stage);
  return (
    <span
      className={cn(
        'inline-flex h-6 items-center gap-1.5 rounded-full px-2.5 text-xs font-medium whitespace-nowrap',
        className,
      )}
      style={{ background: `color-mix(in oklab, ${meta.color} 16%, transparent)` }}
    >
      <span className="size-1.5 rounded-full" style={{ background: meta.color }} />
      {meta.label}
    </span>
  );
}

const STATUS_VARIANT: Record<ClientStatus, 'success' | 'warning' | 'destructive' | 'muted'> = {
  active: 'success',
  paused: 'warning',
  no_response: 'muted',
  refused: 'destructive',
};

export function StatusBadge({ status }: { status: ClientStatus }) {
  return <Badge variant={STATUS_VARIANT[status] ?? 'muted'}>{STATUS_LABELS[status] ?? status}</Badge>;
}

/** «нужен оператор»: AI handed the dialog to a human or could not answer. */
export function NeedsOperatorBadge() {
  return <Badge variant="destructive">нужен оператор</Badge>;
}
