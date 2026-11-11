import { Link } from '@tanstack/react-router';
import {
  AlertCircle, ArrowLeft, Ban, Check, Clock, Info, MessageCircle, PanelRightClose, PanelRightOpen, SendHorizontal, User,
  type LucideIcon,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Avatar } from '@/components/avatar';
import { ChannelIcon, NeedsOperatorBadge, StageChip, StatusBadge } from '@/components/badges';
import { EmptyState } from '@/components/empty-state';
import { MessageContent, MessageMetadata } from '@/components/message-parts';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { Tip } from '@/components/ui/tooltip';
import type { DialogDetail } from '@/lib/models';
import { cn } from '@/lib/utils';
import { deliveryMeta, groupByDay, isSendKey, type DeliveryIcon } from './inbox-logic';

const DELIVERY_ICONS: Record<DeliveryIcon, LucideIcon> = { clock: Clock, check: Check, alert: AlertCircle, ban: Ban };

export function Conversation({
  detail,
  narrow,
  wide,
  cardOpen,
  sending,
  onBack,
  onToggleCard,
  onToggleAi,
  onResolve,
  onSend,
}: {
  detail: DialogDetail;
  narrow: boolean;
  wide: boolean;
  cardOpen: boolean;
  sending: boolean;
  onBack: () => void;
  onToggleCard: () => void;
  onToggleAi: (paused: boolean) => void;
  onResolve: () => void;
  /** Resolves true when the message was accepted (the composer is then cleared). */
  onSend: (text: string) => Promise<boolean>;
}) {
  const { dialog, client, messages } = detail;
  const [text, setText] = useState('');
  const scroller = useRef<HTMLDivElement>(null);
  const groups = groupByDay(messages);

  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, dialog.id]);

  const send = async (): Promise<void> => {
    const value = text.trim();
    if (!value || sending) return;
    if (await onSend(value)) setText('');
  };

  return (
    <>
      <header className="flex flex-none items-center gap-2 border-b px-3 py-2.5 sm:px-4">
        {narrow && (
          <Button variant="ghost" size="icon-sm" aria-label="Назад к списку" onClick={onBack}>
            <ArrowLeft />
          </Button>
        )}
        <Avatar name={client.display_name} url={client.avatar_url} size={36} />
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <strong className="truncate text-sm font-semibold">{client.display_name}</strong>
          <span className="flex flex-wrap items-center gap-1.5">
            {dialog.channel_kind && <ChannelIcon kind={dialog.channel_kind} size={18} />}
            <StageChip stage={client.stage} className="h-5 px-2 text-[11px]" />
            <StatusBadge status={client.status} />
            {dialog.needs_operator && <NeedsOperatorBadge />}
          </span>
        </div>
        {dialog.needs_operator && (
          <Tip label="Снять отметку «нужен оператор»">
            <Button variant="outline" size="sm" onClick={onResolve}>
              Обработано
            </Button>
          </Tip>
        )}
        <Tip label="Когда ИИ выключен, отвечает только оператор">
          <div className="flex items-center gap-2">
            <Switch id="dialog-ai" checked={!dialog.ai_paused} onCheckedChange={(on) => onToggleAi(!on)} />
            <Label htmlFor="dialog-ai">ИИ</Label>
          </div>
        </Tip>
        {wide ? (
          <Button variant="ghost" size="icon-sm" aria-pressed={cardOpen} aria-label="Карточка клиента" onClick={onToggleCard}>
            {cardOpen ? <PanelRightClose /> : <PanelRightOpen />}
          </Button>
        ) : (
          <Button asChild variant="ghost" size="icon-sm">
            <Link to="/clients/$id" params={{ id: String(client.id) }} aria-label="Открыть карточку клиента">
              <User />
            </Link>
          </Button>
        )}
      </header>
      <div className="flex-none border-b px-4 py-1.5">
        <Link
          to="/clients/$id"
          params={{ id: String(client.id) }}
          hash="conversation"
          className="text-xs text-primary hover:underline"
        >
          Лента клиента — все каналы
        </Link>
      </div>

      <div
        ref={scroller}
        tabIndex={0}
        role="log"
        aria-live="polite"
        aria-label="Сообщения"
        className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto bg-muted/30 px-3 py-4 scrollbar-thin sm:px-6"
      >
        {groups.length === 0 && <EmptyState icon={MessageCircle} title="Сообщений пока нет" />}
        {groups.map((g) => (
          <div key={g.key} className="flex flex-col gap-2">
            <div className="my-2 flex justify-center">
              <span className="rounded-full bg-background px-3 py-0.5 text-xs text-muted-foreground shadow-xs">{g.label}</span>
            </div>
            {g.items.map((m) => {
              const blocked = m.msg.guard?.verdict === 'blocked';
              const dm = m.msg.direction === 'out' ? deliveryMeta(m.msg.delivery, m.msg.guard) : null;
              const DIcon = dm ? DELIVERY_ICONS[dm.icon] : null;
              return (
                <div key={m.msg.id} className={cn('flex', m.side === 'right' && 'justify-end')}>
                  <div
                    data-author={m.msg.author}
                    className={cn(
                      'flex max-w-[85%] flex-col gap-1.5 rounded-2xl px-3.5 py-2 text-sm sm:max-w-[70%]',
                      m.msg.author === 'client' && 'rounded-bl-md bg-bubble-client',
                      m.msg.author === 'ai' && 'rounded-br-md bg-bubble-ai',
                      m.msg.author === 'operator' && 'rounded-br-md bg-bubble-operator',
                      blocked && 'border border-dashed border-destructive',
                    )}
                  >
                    {m.authorLabel && <span className="text-[11px] font-medium text-muted-foreground">{m.authorLabel}</span>}
                    <MessageContent message={m.msg} />
                    <MessageMetadata message={m.msg} />
                    <span className="flex items-center justify-end gap-1 text-[11px] text-muted-foreground tabular">
                      {m.time}
                      {dm && DIcon && (
                        <Tip label={dm.label}>
                          <DIcon
                            aria-label={dm.label}
                            className={cn('size-3.5', dm.icon === 'alert' && 'text-destructive')}
                          />
                        </Tip>
                      )}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        ))}
      </div>

      <footer className="flex flex-none flex-col gap-2 border-t bg-background px-3 py-3 sm:px-4">
        {!dialog.ai_paused && (
          <p role="note" className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Info className="size-3.5" />
            Ответ оператора поставит ИИ в этом диалоге на паузу.
          </p>
        )}
        <div className="flex items-end gap-2">
          <Textarea
            aria-label="Сообщение клиенту"
            placeholder="Сообщение клиенту"
            rows={1}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (isSendKey({ key: e.key, shiftKey: e.shiftKey, isComposing: e.nativeEvent.isComposing })) {
                e.preventDefault();
                void send();
              }
            }}
            className="max-h-40 min-h-9"
          />
          <Button size="icon" aria-label="Отправить" disabled={sending || !text.trim()} onClick={() => void send()}>
            <SendHorizontal />
          </Button>
        </div>
        <p className="hidden text-[11px] text-muted-foreground sm:block">Enter — отправить, Shift+Enter — новая строка</p>
      </footer>
    </>
  );
}
