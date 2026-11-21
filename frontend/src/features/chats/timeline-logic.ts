import type { ChatRow, LastMessage, MessageKind, TimelineItem } from '@/lib/types';

export const MESSAGE_KIND_LABEL: Record<MessageKind, string> = {
  text: '',
  photo: 'Фото',
  video: 'Видео',
  voice: 'Голосовое',
  video_note: 'Видеосообщение',
  file: 'Файл',
  sticker: 'Стикер',
  call: 'Звонок',
};

/** One line under the client's name in the chat list. */
export function lastMessagePreview(m: LastMessage | null): string {
  if (!m) return 'Нет сообщений';
  const prefix = m.author === 'operator' ? 'Оператор: ' : m.author === 'persona' ? 'Она: ' : '';
  const kind = MESSAGE_KIND_LABEL[m.kind] ?? '';
  const text = (m.text ?? '').trim();
  const body = kind && text ? `${kind} · ${text}` : kind || text || '…';
  return `${prefix}${body}`;
}

/** The persona answers in this chat only when both the chat and the client allow it. */
export function aiActive(chat: Pick<ChatRow, 'ai_enabled'> & { client: { ai_enabled: boolean } }): boolean {
  return chat.ai_enabled && chat.client.ai_enabled;
}

export const CALL_STATUS_LABEL: Record<string, string> = {
  ringing: 'звонит',
  active: 'идёт',
  ended: 'завершён',
  missed: 'пропущен',
  declined: 'отклонён',
  failed: 'сбой',
};

export const GENERATION_KIND_LABEL: Record<string, string> = {
  photo: 'фото', video: 'видео', voice: 'голосовое', video_note: 'кружка',
};

export const GENERATION_STATUS_LABEL: Record<string, string> = {
  queued: 'в очереди',
  running: 'готовится',
  done: 'готово',
  failed: 'не получилось',
};

export type Tone = 'muted' | 'warning' | 'danger';

export interface SystemLine {
  text: string;
  tone: Tone;
}

function str(data: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const v = data[key];
    if (typeof v === 'string' && v.trim()) return v.trim();
  }
  return '';
}

const withReason = (base: string, reason: string): string => (reason ? `${base}: ${reason}` : base);

/**
 * Journal events shown as a small centred line (not a bubble). Null for kinds rendered
 * otherwise (messages, calls, generations) or not shown at all (`media_understood` only
 * fills in the message it belongs to).
 */
export function systemLine(item: TimelineItem): SystemLine | null {
  const d = item.data ?? {};
  switch (item.kind) {
    case 'message_edited': {
      const before = str(d, 'old_text', 'old', 'before');
      const after = str(d, 'new_text', 'new', 'after', 'text');
      return { text: before ? `Клиент исправил сообщение: «${before}» → «${after}»` : `Клиент исправил сообщение: «${after}»`, tone: 'muted' };
    }
    case 'message_deleted':
      return { text: withReason('Клиент удалил сообщение', str(d, 'text') && `«${str(d, 'text')}»`), tone: 'muted' };
    case 'send_failed':
      return item.message ? null : { text: withReason('Не доставлено', str(d, 'error', 'reason')), tone: 'danger' };
    case 'guard_blocked':
      return { text: withReason('Проверка заблокировала ответ', str(d, 'reason')), tone: 'warning' };
    case 'operator_needed':
      return { text: withReason('Нужен оператор', str(d, 'reason')), tone: 'warning' };
    case 'ai_toggled':
      return { text: d.enabled === false ? 'ИИ выключен в этом чате' : 'ИИ включён в этом чате', tone: 'muted' };
    case 'operator_note':
      return { text: `Заметка оператора: ${str(d, 'text', 'note')}`, tone: 'muted' };
    case 'client_updated':
      return { text: 'Карточка клиента обновлена', tone: 'muted' };
    case 'memory_updated':
      return { text: 'Память и сводка обновлены', tone: 'muted' };
    default:
      return null;
  }
}

/** Has a persona bubble whose turn can be explained («Почему так»). */
export function canExplain(item: TimelineItem): boolean {
  return item.reply_run_id !== null && item.message?.author === 'persona';
}
