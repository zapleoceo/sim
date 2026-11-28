import {
  AlertCircle, Check, CheckCheck, Clock, Image as ImageIcon, Loader2, PhoneIncoming, PhoneMissed, PhoneOutgoing,
  Sparkles, Video, Mic, XCircle,
} from 'lucide-react';
import { ChannelIcon } from '@/components/channel-icon';
import { formatDuration, formatTime } from '@/lib/format';
import type { TimelineCall, TimelineGeneration, TimelineItem, TimelineMessage } from '@/lib/types';
import { cn } from '@/lib/utils';
import { MediaView } from './media-view';
import {
  CALL_STATUS_LABEL, GENERATION_KIND_LABEL, GENERATION_STATUS_LABEL, MESSAGE_KIND_LABEL, canExplain, systemLine, type Tone,
} from './timeline-logic';

const BUBBLE_BG = {
  client: 'bg-bubble-client',
  persona: 'bg-bubble-ai',
  operator: 'bg-bubble-operator',
} as const;

const TONE_CLASS: Record<Tone, string> = {
  muted: 'text-muted-foreground',
  warning: 'text-warning',
  danger: 'text-destructive',
};

function DeliveryIcon({ message }: { message: TimelineMessage }) {
  if (message.direction === 'in') return null;
  if (message.status === 'pending') return <Clock className="size-3" aria-label="Отправляется" />;
  if (message.status === 'failed') return <AlertCircle className="size-3 text-destructive" aria-label="Не доставлено" />;
  if (message.status === 'sent') return <CheckCheck className="size-3" aria-label="Доставлено" />;
  return <Check className="size-3" aria-hidden="true" />;
}

/**
 * A sticker's emoji (`message.text`) with a small «стикер»: large on its own (her sent stickers have
 * no file), smaller under the picture of an incoming one. Never an empty bubble.
 */
function StickerLine({ emoji, large }: { emoji: string; large: boolean }) {
  return (
    <p className="flex items-end gap-2">
      {emoji && (
        <span role="img" aria-label={`Стикер ${emoji}`} className={cn('leading-none', large ? 'text-4xl' : 'text-2xl')}>
          {emoji}
        </span>
      )}
      <span className="text-[11px] text-muted-foreground">стикер</span>
    </p>
  );
}

function MessageBubble({ item, message, onExplain, channel }: {
  item: TimelineItem;
  message: TimelineMessage;
  onExplain: (runId: number) => void;
  channel?: string | null;
}) {
  const mine = message.author !== 'client';
  const failed = message.status === 'failed' || item.kind === 'send_failed';
  const text = (message.text ?? '').trim();
  const authorLabel = message.author === 'client' ? 'Клиент' : message.author === 'operator' ? 'Оператор' : 'Персонаж';
  return (
    <div
      className={cn('flex max-w-[88%] flex-col gap-1 sm:max-w-[70%]', mine ? 'items-end self-end' : 'items-start self-start')}
      data-author={message.author}
    >
      {message.author === 'operator' && (
        <span className="px-1 text-[11px] font-medium text-muted-foreground">оператор</span>
      )}
      <div
        aria-label={`${authorLabel}, ${formatTime(message.sent_at ?? item.created_at)}`}
        className={cn(
          'flex min-w-0 max-w-full flex-col gap-2 rounded-2xl px-3 py-2 text-sm',
          BUBBLE_BG[message.author],
          mine ? 'rounded-br-md' : 'rounded-bl-md',
          failed && 'border border-destructive/60 bg-destructive/10',
        )}
      >
        {message.media && <MediaView media={message.media} messageKind={message.kind} />}
        {message.kind === 'sticker' ? (
          <StickerLine emoji={text} large={!message.media} />
        ) : (
          text && <p className="break-words whitespace-pre-wrap">{text}</p>
        )}
        {message.kind !== 'sticker' && !message.media && !text && (
          <p className="text-muted-foreground italic">{MESSAGE_KIND_LABEL[message.kind] || 'Пустое сообщение'}</p>
        )}
      </div>
      <div className="flex items-center gap-1.5 px-1 text-[11px] text-muted-foreground">
        {channel && <ChannelIcon kind={channel} className="size-3" />}
        <time dateTime={message.sent_at ?? item.created_at} className="tabular">
          {formatTime(message.sent_at ?? item.created_at)}
        </time>
        <DeliveryIcon message={message} />
        {canExplain(item) && item.reply_run_id !== null && (
          <button
            type="button"
            onClick={() => onExplain(item.reply_run_id as number)}
            className="ml-1 rounded px-1 font-medium text-primary hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          >
            Почему так
          </button>
        )}
      </div>
      {failed && (
        <p role="alert" className="px-1 text-xs text-destructive">
          Не доставлено{message.error ? `: ${message.error}` : ''}
        </p>
      )}
    </div>
  );
}

const WHO: Record<string, string> = { client: 'Клиент', persona: 'Она' };

export function CallCard({ call, at }: { call: TimelineCall; at: string }) {
  const missed = call.status === 'missed' || call.status === 'declined' || call.status === 'failed';
  const Icon = missed ? PhoneMissed : call.direction === 'in' ? PhoneIncoming : PhoneOutgoing;
  const title = call.direction === 'in' ? 'Входящий звонок' : 'Исходящий звонок';
  const duration = formatDuration(call.duration_s);
  return (
    <div className="flex w-full max-w-md flex-col gap-2 self-center rounded-xl border bg-card p-3 text-sm shadow-xs">
      <div className="flex items-center gap-2.5">
        <span className={cn('flex size-8 flex-none items-center justify-center rounded-full bg-muted', missed && 'text-destructive')}>
          <Icon className="size-4" />
        </span>
        <div className="flex min-w-0 flex-col">
          <span className="font-medium">{title}</span>
          <span className="text-xs text-muted-foreground">
            {[formatTime(at), duration, CALL_STATUS_LABEL[call.status] ?? call.status].filter(Boolean).join(' · ')}
          </span>
        </div>
      </div>
      {call.recording_url && (
        // The server's URL as is, like media files (same cookie session, Range for seeking).
        <div className="flex flex-col gap-1">
          <span className="text-xs font-medium text-muted-foreground">
            Запись разговора{call.recording_s ? ` · ${formatDuration(call.recording_s)}` : ''}
          </span>
          <audio controls preload="none" src={call.recording_url} aria-label="Запись разговора" className="h-10 w-full" />
        </div>
      )}
      {call.transcript.length > 0 && (
        <details className="group rounded-md bg-muted/50 px-2.5 py-1.5">
          <summary className="cursor-pointer text-xs font-medium text-muted-foreground select-none">
            Расшифровка ({call.transcript.length})
          </summary>
          <ol className="mt-2 flex flex-col gap-1.5">
            {call.transcript.map((line, i) => (
              <li key={i} className="text-xs leading-snug">
                {line.t !== null && <span className="mr-1 text-muted-foreground tabular">{formatDuration(line.t)}</span>}
                <span className="font-medium">{WHO[line.who] ?? line.who}: </span>
                <span className="whitespace-pre-wrap">{line.text}</span>
              </li>
            ))}
          </ol>
        </details>
      )}
    </div>
  );
}

const GEN_ICON = { photo: ImageIcon, video: Video, voice: Mic, video_note: Video } as const;

export function GenerationChip({ generation }: { generation: TimelineGeneration }) {
  const failed = generation.status === 'failed';
  const busy = generation.status === 'queued' || generation.status === 'running';
  const KindIcon = GEN_ICON[generation.kind] ?? Sparkles;
  const StatusIcon = busy ? Loader2 : failed ? XCircle : Check;
  return (
    <div className="flex max-w-[90%] flex-col items-center gap-1 self-center">
      <span
        className={cn(
          'inline-flex max-w-full items-center gap-1.5 rounded-full border px-3 py-1 text-xs',
          failed ? 'border-destructive/50 bg-destructive/10 text-destructive' : 'bg-muted/60 text-muted-foreground',
        )}
      >
        <KindIcon className="size-3.5 flex-none" />
        <span className="truncate">
          Генерация {GENERATION_KIND_LABEL[generation.kind] ?? generation.kind}
          {generation.caption ? ` «${generation.caption}»` : ''} — {GENERATION_STATUS_LABEL[generation.status] ?? generation.status}
        </span>
        <StatusIcon className={cn('size-3.5 flex-none', busy && 'animate-spin')} />
      </span>
      {failed && generation.error && <span className="text-center text-xs text-destructive">{generation.error}</span>}
    </div>
  );
}

/** One row of the conversation, by journal event kind. */
/**
 * One journal event. `channel` — the messenger the message came through, shown as a small icon
 * by its time in a feed over several messengers (the person's conversation).
 */
export function TimelineEntry({ item, onExplain, channel }: {
  item: TimelineItem;
  onExplain: (runId: number) => void;
  channel?: string | null;
}) {
  if (item.message && (item.kind === 'message_in' || item.kind === 'message_out' || item.kind === 'send_failed')) {
    return <MessageBubble item={item} message={item.message} onExplain={onExplain} channel={channel} />;
  }
  if (item.kind === 'call' && item.call) return <CallCard call={item.call} at={item.created_at} />;
  if ((item.kind === 'generation_started' || item.kind === 'generation_failed') && item.generation) {
    return <GenerationChip generation={item.generation} />;
  }
  const line = systemLine(item);
  if (!line) return null;
  return (
    <p className={cn('max-w-[90%] self-center text-center text-xs leading-snug whitespace-pre-wrap', TONE_CLASS[line.tone])}>
      {line.text}
      <span className="ml-1.5 opacity-70 tabular">{formatTime(item.created_at)}</span>
    </p>
  );
}

