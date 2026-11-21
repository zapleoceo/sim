import { describe, expect, it } from 'vitest';
import { relativeUntil, statusText, untilLine } from './persona-status-logic';

const NOW = new Date('2026-11-21T19:28:00Z');

describe('relativeUntil', () => {
  it('counts minutes and hours to the end of the pause', () => {
    expect(relativeUntil('2026-11-21T19:40:00Z', NOW)).toBe('через 12 мин');
    expect(relativeUntil('2026-11-21T19:39:30Z', NOW)).toBe('через 12 мин'); // rounded up
    expect(relativeUntil('2026-11-21T20:33:00Z', NOW)).toBe('через 1 ч 5 мин');
    expect(relativeUntil('2026-11-21T21:28:00Z', NOW)).toBe('через 2 ч');
  });

  it('says when it is about to end or already passed, and nothing without a time', () => {
    expect(relativeUntil('2026-11-21T19:28:40Z', NOW)).toBe('меньше чем через минуту');
    expect(relativeUntil('2026-11-21T19:20:00Z', NOW)).toBe('уже сейчас');
    expect(relativeUntil(null, NOW)).toBe('');
  });
});

describe('untilLine', () => {
  it('names her local time and the relative time', () => {
    expect(untilLine({ until: '2026-11-21T19:40:00Z', local_until: '20:40' }, NOW)).toBe(
      'до 20:40 по её времени · через 12 мин',
    );
    expect(untilLine({ until: null, local_until: '20:40' }, NOW)).toBe('до 20:40 по её времени');
    expect(untilLine({ until: null, local_until: null }, NOW)).toBe('');
  });
});

describe('statusText', () => {
  it('joins the title and the reason', () => {
    expect(statusText({ title: 'Оставила прочитанным', detail: 'он просто поблагодарил' })).toBe(
      'Оставила прочитанным — он просто поблагодарил',
    );
    expect(statusText({ title: 'Думает над ответом', detail: '' })).toBe('Думает над ответом');
  });
});
