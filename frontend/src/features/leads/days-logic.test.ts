import { describe, expect, it } from 'vitest';
import type { ChatDay } from '@/lib/types';
import {
  canRebuild,
  chatDayTitle,
  chatOptionLabel,
  chatWeekTitle,
  dayPatch,
  dayState,
  parseFacts,
  toDraft,
} from './days-logic';

function chatDay(patch: Partial<ChatDay> = {}): ChatDay {
  return {
    day: '2026-11-20', summary: 'Дима рассказал, что он врач.', facts: ['врач', 'дочь Аня'], source: 'model',
    model: 'codex-sub/gpt-6.1-sol', error: null, first_event_id: 1, last_event_id: 9,
    updated_at: '2026-11-21T00:05:00Z', ...patch,
  };
}

describe('days logic', () => {
  it('turns the facts textarea into a list and back', () => {
    expect(parseFacts(' врач \n\n дочь Аня\n')).toEqual(['врач', 'дочь Аня']);
    expect(toDraft(chatDay())).toEqual({ summary: 'Дима рассказал, что он врач.', facts: 'врач\nдочь Аня' });
  });

  it('sends only a real change with a summary', () => {
    const day = chatDay();
    expect(dayPatch(toDraft(day), day)).toBeNull();
    expect(dayPatch({ summary: ' Дима рассказал, что он врач. ', facts: 'врач\n дочь Аня ' }, day)).toBeNull();
    expect(dayPatch({ summary: '   ', facts: '' }, day)).toBeNull();
    expect(dayPatch({ summary: 'Иначе.', facts: 'врач' }, day)).toEqual({ summary: 'Иначе.', facts: ['врач'] });
  });

  it('names the state, the day, the week and the chat', () => {
    expect(dayState(chatDay())).toBe('model');
    expect(dayState(chatDay({ source: 'edited' }))).toBe('edited');
    expect(dayState(chatDay({ summary: '', error: 'сбой' }))).toBe('error');
    expect(chatDayTitle(chatDay())).toBe('пятница, 20 ноября');
    expect(chatWeekTitle({ week: '2026-11-16', summary: '', facts: [], source: 'model', model: '', error: null, first_event_id: null, last_event_id: null, updated_at: '' })).toBe('Неделя с 16 ноября');
    expect(chatOptionLabel({ id: 7, persona: { id: 1, name: 'Марина', avatar_media_id: null } })).toBe('Марина · чат №7');
    expect(chatOptionLabel({ id: 8, persona: null })).toBe('Чат №8');
  });

  it('rebuilds only finished days', () => {
    expect(canRebuild('2026-11-20', '2026-11-21')).toBe(true);
    expect(canRebuild('2026-11-21', '2026-11-21')).toBe(false);
  });
});
