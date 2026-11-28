import { describe, expect, it } from 'vitest';
import { mergeBlocker, olderFirst } from './merge-logic';

describe('merging two leads by hand', () => {
  const dima = { id: 1, messengers: ['telegram_user', 'whatsapp'], created_at: '2026-11-20T10:00:00Z' };
  const instagram = { id: 8, messengers: ['instagram'], created_at: '2026-11-28T10:00:00Z' };

  it('allows leads in different messengers and refuses one messenger twice', () => {
    expect(mergeBlocker(dima, instagram)).toBeNull();
    expect(mergeBlocker(dima, { id: 9, messengers: ['whatsapp'] })).toContain('WhatsApp');
    expect(mergeBlocker(dima, dima)).toBe('Это тот же лид');
  });

  it('keeps the older lead', () => {
    expect(olderFirst(instagram, dima).map((l) => l.id)).toEqual([1, 8]);
    expect(olderFirst({ id: 5, messengers: [] }, { id: 3, messengers: [] }).map((l) => l.id)).toEqual([3, 5]);
  });
});
