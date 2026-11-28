import { channelLook } from '@/components/channel-icon';
import type { ChannelKind } from '@/lib/types';

export interface EmptyText {
  title: string;
  hint: string;
  /** Some filter is on: the empty list offers «Сбросить фильтры». */
  filtered: boolean;
}

/** «А, Б и В» */
function joinRu(words: readonly string[]): string {
  return words.length < 2 ? words.join('') : `${words.slice(0, -1).join(', ')} и ${words[words.length - 1]}`;
}

/**
 * What the empty chat list says: nothing at all yet; nothing in the chosen messengers; nothing
 * found by the search (the search names what was typed).
 */
export function emptyChatsText({ q, sources, persona }: { q: string; sources: readonly ChannelKind[]; persona: number }): EmptyText {
  const where = sources.length ? ` в ${joinRu(sources.map((kind) => channelLook(kind).label))}` : '';
  if (q) {
    return { title: `Ничего не найдено по «${q}»${where}`, hint: 'Проверьте имя, username или слово из переписки.', filtered: true };
  }
  if (sources.length || persona) {
    return { title: `Чатов${where} пока нет`, hint: 'Выберите другой источник или сбросьте фильтры.', filtered: true };
  }
  return {
    title: 'Чатов пока нет',
    hint: 'Чат появится, когда клиент напишет персонажу в подключённый мессенджер.',
    filtered: false,
  };
}
