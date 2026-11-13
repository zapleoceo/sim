import { AlertCircle, Ban, Check, Clock, type LucideIcon } from 'lucide-react';
import { MessageContent, MessageMetadata } from '@/components/message-parts';
import { Tip } from '@/components/ui/tooltip';
import { deliveryMeta, type DeliveryIcon, type MessageVm } from '@/features/inbox/inbox-logic';
import { cn } from '@/lib/utils';

const DELIVERY_ICONS: Record<DeliveryIcon, LucideIcon> = { clock: Clock, check: Check, alert: AlertCircle, ban: Ban };

/**
 * One chat bubble: the client on the left, the persona and the operator on the right, time and
 * delivery state under the text. `details` adds who generated it and what it cost (the inbox);
 * the client card keeps it calm and shows the text only.
 */
export function MessageBubble({ vm, details = false }: { vm: MessageVm; details?: boolean }) {
  const { msg } = vm;
  const blocked = msg.guard?.verdict === 'blocked';
  const dm = msg.direction === 'out' ? deliveryMeta(msg.delivery, msg.guard) : null;
  const DIcon = dm ? DELIVERY_ICONS[dm.icon] : null;
  return (
    <div className={cn('flex', vm.side === 'right' && 'justify-end')}>
      <div
        data-author={msg.author}
        className={cn(
          'flex max-w-[85%] flex-col gap-1.5 rounded-2xl px-3.5 py-2 text-sm sm:max-w-[70%]',
          msg.author === 'client' && 'rounded-bl-md bg-bubble-client',
          msg.author === 'ai' && 'rounded-br-md bg-bubble-ai',
          msg.author === 'operator' && 'rounded-br-md bg-bubble-operator',
          blocked && 'border border-dashed border-destructive',
        )}
      >
        {vm.authorLabel && <span className="text-[11px] font-medium text-muted-foreground">{vm.authorLabel}</span>}
        <MessageContent message={msg} />
        {details && <MessageMetadata message={msg} />}
        <span className="flex items-center justify-end gap-1 text-[11px] text-muted-foreground tabular">
          {vm.time}
          {dm && DIcon && (
            <Tip label={dm.label}>
              <DIcon aria-label={dm.label} className={cn('size-3.5', dm.icon === 'alert' && 'text-destructive')} />
            </Tip>
          )}
        </span>
      </div>
    </div>
  );
}
