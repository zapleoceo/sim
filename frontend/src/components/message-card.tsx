import { Link } from '@tanstack/react-router';
import type { MessageActivity } from '@/lib/activity-models';
import { formatDateTime } from '@/lib/format';
import type { Author, Delivery } from '@/lib/models';
import { CHANNEL_LABELS } from '@/lib/stages';
import { cn } from '@/lib/utils';
import { ChannelIcon } from './badges';
import { MessageContent, MessageMetadata } from './message-parts';
import { Button } from './ui/button';

export const DELIVERY_LABELS: Record<Delivery, string> = {
  queued: 'В очереди',
  sent: 'Отправлено',
  failed: 'Ошибка · нужна сверка',
  cancelled: 'Отменено',
};

export const AUTHOR_LABELS: Record<Author, string> = { client: 'Клиент', ai: 'ИИ', operator: 'Оператор' };

/** One stored message with its channel, client, persona and delivery state (activity feeds). */
export function MessageCard({ message }: { message: MessageActivity }) {
  const ch = message.channel;
  return (
    <article
      data-author={message.author}
      className={cn(
        'flex flex-col gap-3 rounded-xl border bg-card p-4 text-sm shadow-xs [overflow-wrap:anywhere]',
        message.author === 'ai' && 'border-l-4 border-l-primary',
        message.author === 'operator' && 'border-l-4 border-l-success',
      )}
    >
      <header className="flex flex-wrap items-center justify-between gap-2">
        <strong className="font-medium">
          {message.direction === 'in' ? 'Входящее' : 'Исходящее'} · {AUTHOR_LABELS[message.author]}
        </strong>
        <time dateTime={message.created_at} className="text-xs text-muted-foreground">
          {formatDateTime(message.created_at)}
        </time>
      </header>
      <div className="flex items-center gap-2 text-muted-foreground">
        <ChannelIcon kind={ch.kind} size={20} />
        <span className="min-w-0 flex-1">
          {CHANNEL_LABELS[ch.kind]} · {ch.name}
          {ch.account_label ? ` · ${ch.account_label}` : ''}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Link to="/clients/$id" params={{ id: String(message.client.id) }} className="font-medium text-primary hover:underline">
          {message.client.display_name}
        </Link>
        <span className="text-muted-foreground">Персонаж: {message.persona.name}</span>
      </div>
      <MessageContent message={message} />
      <MessageMetadata message={message} />
      <footer className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span>
          № {message.id}
          {message.direction === 'out' ? ` · ${DELIVERY_LABELS[message.delivery]}` : ''}
        </span>
        {message.sent_at && <span>Отправка подтверждена: {formatDateTime(message.sent_at)}</span>}
        <Button asChild variant="ghost" size="sm" className="ml-auto">
          <Link to="/inbox" search={{ d: message.dialog_id }}>
            Открыть диалог
          </Link>
        </Button>
      </footer>
    </article>
  );
}
