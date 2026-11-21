import { dayTitle } from '@/features/persona/life/life-logic';
import type { ChatDay, ChatDayPatch, ChatRow, ChatWeek } from '@/lib/types';

/** The textarea of facts: one fact per line. */
export function factsText(facts: string[]): string {
  return facts.join('\n');
}

/** Lines of the facts textarea: trimmed, without empty ones. */
export function parseFacts(text: string): string[] {
  return text.split('\n').map((line) => line.trim()).filter(Boolean);
}

export interface DayDraft {
  summary: string;
  facts: string;
}

export const toDraft = (day: ChatDay): DayDraft => ({ summary: day.summary, facts: factsText(day.facts) });

/** The PUT body, or null when nothing changed or the summary is empty. */
export function dayPatch(draft: DayDraft, day: ChatDay): ChatDayPatch | null {
  const summary = draft.summary.trim();
  const facts = parseFacts(draft.facts);
  if (!summary) return null;
  if (summary === day.summary.trim() && JSON.stringify(facts) === JSON.stringify(day.facts)) return null;
  return { summary, facts };
}

export type DayState = 'model' | 'edited' | 'error';

/** How the day was written: by the chronicler, by the owner, or a failed night. */
export function dayState(day: ChatDay): DayState {
  if (day.source === 'edited') return 'edited';
  return day.error && !day.summary.trim() ? 'error' : 'model';
}

export const DAY_STATE_LABEL: Record<DayState, string> = {
  model: 'Летописец',
  edited: 'изменён вручную',
  error: 'ошибка',
};

/** «пятница, 20 ноября». */
export const chatDayTitle = (day: ChatDay): string => dayTitle(day.day);

/** «Неделя с 16 ноября». */
export const chatWeekTitle = (week: ChatWeek): string => `Неделя с ${dayTitle(week.week).split(', ')[1] ?? week.week}`;

/** How a chat is named in the chat picker: its persona and number. */
export function chatOptionLabel(chat: Pick<ChatRow, 'id' | 'persona'>): string {
  return chat.persona ? `${chat.persona.name} · чат №${chat.id}` : `Чат №${chat.id}`;
}

/** A day can be rebuilt only after her midnight (today has no summary yet). */
export const canRebuild = (day: string, today: string): boolean => day < today;
