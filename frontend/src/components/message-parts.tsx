import { FileText, Image, Mic, Paperclip, Phone, Video, type LucideIcon } from 'lucide-react';
import { formatInt, formatMoney } from '@/lib/format';
import type { Message } from '@/lib/models';
import { GuardBadge } from './guard-badge';
import { MediaDecisionNote } from './media-decision-note';

const MEDIA: Record<string, { icon: LucideIcon; label: string }> = {
  image: { icon: Image, label: 'Фото' },
  photo: { icon: Image, label: 'Фото' },
  video: { icon: Video, label: 'Видео' },
  voice: { icon: Mic, label: 'Голосовое' },
  audio: { icon: Mic, label: 'Аудио' },
  document: { icon: FileText, label: 'Файл' },
  call: { icon: Phone, label: 'Звонок' },
};

export function mediaMeta(kind: string | null | undefined): { icon: LucideIcon; label: string } | null {
  return kind ? (MEDIA[kind] ?? { icon: Paperclip, label: 'Вложение' }) : null;
}

/** Message body: media, text, guard verdict and delivery failure. */
export function MessageContent({ message }: { message: Message }) {
  if (message.media_decision) return <MediaDecisionNote decision={message.media_decision} />;
  const media = mediaMeta(message.media_kind);
  const url = message.media_url;
  return (
    <div className="flex min-w-0 flex-col gap-2">
      {media &&
        (media.icon === Image && url ? (
          <a href={url} target="_blank" rel="noopener noreferrer">
            <img src={url} alt={media.label} loading="lazy" className="block max-h-72 max-w-full rounded-md object-contain" />
          </a>
        ) : media.icon === Mic && url ? (
          <audio controls preload="none" src={url} className="w-full max-w-80" />
        ) : (
          <span className="flex items-center gap-2 text-sm">
            <media.icon className="size-4" />
            {url ? (
              <a href={url} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2">
                {media.label}
              </a>
            ) : (
              media.label
            )}
          </span>
        ))}
      {message.text && <p className="whitespace-pre-wrap [overflow-wrap:anywhere]">{message.text}</p>}
      {message.author === 'ai' && <GuardBadge guard={message.guard} />}
      {message.delivery === 'failed' && (
        <p className="text-xs text-destructive">Ошибка отправки. Результат требует сверки с каналом.</p>
      )}
    </div>
  );
}

export function metric(value: number | null | undefined): string {
  return value == null ? '—' : formatInt(value);
}

export function costLabel(value: number | null | undefined): string {
  return value == null ? '—' : formatMoney(value, 'USD', 6);
}

/** Who wrote it and what it cost (operator name, generator model, tokens). */
export function MessageMetadata({ message }: { message: Message }) {
  if (message.author === 'operator') {
    return <p className="text-xs text-muted-foreground">Оператор: {message.operator_name || 'имя не сохранено'}</p>;
  }
  if (message.author !== 'ai' || message.media_decision) return null;
  return (
    <p className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted-foreground [overflow-wrap:anywhere]">
      <span>Генератор: {message.model || 'модель не сохранена'}</span>
      <span>Провайдер: {message.provider || '—'}</span>
      <span className="tabular">
        Токены: вход {metric(message.tokens_in)} · выход {metric(message.tokens_out)}
      </span>
      <span className="tabular">Стоимость: {costLabel(message.cost_usd)}</span>
    </p>
  );
}
