import type { Sticker } from '@/lib/types';

/** How many stickers of one group the «Стикеры» block shows; the rest is «ещё N». */
export const STICKERS_PER_GROUP = 40;

export const FAVOURITES_TITLE = 'Избранное';

export interface StickerGroup {
  key: string;
  title: string;
  /** The shown stickers, at most `STICKERS_PER_GROUP`. */
  stickers: Sticker[];
  /** How many more the group has beyond the shown ones. */
  hidden: number;
}

/**
 * Her stickers by group: «Избранное» (every faved sticker) first, then one group per set by its
 * title (the short name if the title is empty), in the server's order. A faved sticker sits only
 * in «Избранное».
 */
export function groupStickers(items: Sticker[], limit = STICKERS_PER_GROUP): StickerGroup[] {
  const groups = new Map<string, { title: string; stickers: Sticker[] }>();
  const add = (key: string, title: string, sticker: Sticker): void => {
    const group = groups.get(key) ?? { title, stickers: [] };
    group.stickers.push(sticker);
    groups.set(key, group);
  };
  const faved = items.filter((s) => s.faved);
  if (faved.length > 0) groups.set('faved', { title: FAVOURITES_TITLE, stickers: faved });
  for (const s of items) {
    if (s.faved) continue;
    const title = s.set_title.trim() || s.set_name.trim() || 'Без набора';
    add(`set:${s.set_name || title}`, title, s);
  }
  return [...groups].map(([key, g]) => ({
    key,
    title: g.title,
    stickers: g.stickers.slice(0, limit),
    hidden: Math.max(g.stickers.length - limit, 0),
  }));
}

/** The sticker's emoji as shown in the block; a sticker without one shows «?». */
export function stickerEmoji(s: Pick<Sticker, 'emoji'>): string {
  return s.emoji.trim() || '?';
}
