import { describe, expect, it } from 'vitest';
import type { Sticker } from '@/lib/types';
import { FAVOURITES_TITLE, groupStickers, stickerEmoji } from './sticker-logic';

function sticker(id: number, patch: Partial<Sticker> = {}): Sticker {
  return { id, channel_id: 4, emoji: '😀', set_name: 'cats', set_title: 'Котики', faved: false, ...patch };
}

describe('groupStickers', () => {
  it('«Избранное» first, then each set by title in the server order', () => {
    const groups = groupStickers([
      sticker(1, { emoji: '🐱' }),
      sticker(2, { emoji: '🐶', set_name: 'dogs', set_title: 'Собачки' }),
      sticker(3, { emoji: '😘', faved: true }),
      sticker(4, { emoji: '😺' }),
    ]);
    expect(groups.map((g) => g.title)).toEqual([FAVOURITES_TITLE, 'Котики', 'Собачки']);
    expect(groups[0].stickers.map((s) => s.emoji)).toEqual(['😘']);          // a faved one sits only there
    expect(groups[1].stickers.map((s) => s.emoji)).toEqual(['🐱', '😺']);
    expect(groups.every((g) => g.hidden === 0)).toBe(true);
  });

  it('no favourites — no «Избранное»; a set without a title goes by its short name', () => {
    const groups = groupStickers([sticker(1, { set_title: '', set_name: 'pack_by_bot' }), sticker(2, { set_title: '', set_name: '' })]);
    expect(groups.map((g) => g.title)).toEqual(['pack_by_bot', 'Без набора']);
  });

  it('shows at most the limit per group and counts the rest', () => {
    const items = Array.from({ length: 45 }, (_, i) => sticker(i + 1));
    const [group] = groupStickers(items, 40);
    expect(group.stickers).toHaveLength(40);
    expect(group.hidden).toBe(5);
  });

  it('nothing for no stickers', () => {
    expect(groupStickers([])).toEqual([]);
  });
});

describe('stickerEmoji', () => {
  it('the emoji, or «?» when the sticker has none', () => {
    expect(stickerEmoji({ emoji: '🐱' })).toBe('🐱');
    expect(stickerEmoji({ emoji: ' ' })).toBe('?');
  });
});
