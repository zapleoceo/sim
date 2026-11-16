import { AlertCircle, Ban, Check, Clock, type LucideIcon } from 'lucide-react';
import { MessageContent, MessageMetadata } from '@/components/message-parts';
import { Tip } from '@/components/ui/tooltip';
import { deliveryMeta, type DeliveryIcon, type MessageVm } from '@/features/inbox/inbox-logic';
import { cn } from '@/lib/utils';
import { voiceNotice } from '@/lib/voice-notice';
import { formatZonedDateTime } from '@/lib/format';

const DELIVERY_ICONS: Record<DeliveryIcon, LucideIcon> = { clock: Clock, check: Check, alert: AlertCircle, ban: Ban };

/**
 * One chat bubble: the client on the left, the persona and the operator on the right, time and
 * delivery state under the text. `details` adds who generated it and what it cost (the inbox);
 * the client card keeps it calm and shows the text only.
 */
export function MessageBubble({ vm, details = false }: { vm: MessageVm; details?: boolean }) {
  const { msg } = vm;
  if (msg.media_decision) {
    // A service record, not a message: a full-width line of its own, without a delivery state.
    return (
      <div data-service="media-decision" className="rounded-lg border border-dashed bg-muted/40 px-3 py-2">
        <MessageContent message={msg} />
        <span className="mt-1 block text-right text-[11px] text-muted-foreground tabular">{vm.time}</span>
      </div>
    );
  }
  const blocked = msg.guard?.verdict === 'blocked';
  const voiceReason = voiceNotice(msg.handoff_reason || msg.error);
  const dm = msg.direction === 'out' ? deliveryMeta(msg.delivery, msg.guard) : null;
  const DIcon = dm ? DELIVERY_ICONS[dm.icon] : null;
  return (
    <div className={cn('flex', vm.side === 'right' && 'justify-end')}>
      <div
        data-author={msg.author}
        className={cn(
          'flex min-w-0 max-w-[92%] flex-col gap-1.5 rounded-2xl px-3.5 py-2 text-sm sm:max-w-[70%]',
          msg.author === 'client' && 'rounded-bl-md bg-bubble-client',
          msg.author === 'ai' && 'rounded-br-md bg-bubble-ai',
          msg.author === 'operator' && 'rounded-br-md bg-bubble-operator',
          blocked && 'border border-dashed border-destructive',
        )}
      >
        <span className="text-[11px] font-medium text-muted-foreground">{vm.authorLabel || (msg.author === 'client' ? 'Клиент' : msg.author === 'ai' ? 'Персонаж · ИИ' : 'Оператор')}</span>
        <MessageContent message={msg} />
        {msg.media_kind === 'call' && <span className="text-xs text-muted-foreground">{msg.error ? 'Техническая проблема · можно продолжить в чате' : 'Звонок завершён · расшифровка ниже'}</span>}
        {details && (msg.model || msg.provider || msg.operator_name || msg.tokens_in != null || msg.tokens_out != null || msg.cost_usd != null) &&
          <details className="text-xs text-muted-foreground"><summary className="cursor-pointer">Данные сообщения</summary><MessageMetadata message={msg} /></details>}
        {voiceReason && <div className="text-xs text-warning"><span role="note">{voiceReason}</span>
          <details><summary>Подробности</summary>{msg.handoff_reason || msg.error}</details>
        </div>}
        {msg.handoff_reason && !voiceReason && <span role="note" className="text-xs text-warning">
          Передача оператору в истории: {msg.handoff_reason}
        </span>}
        <span className="flex items-center justify-end gap-1 text-[11px] text-muted-foreground tabular">
          <time dateTime={msg.created_at} title={`${formatZonedDateTime(msg.created_at)} (ваше время)`}>{vm.time}</time>
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
