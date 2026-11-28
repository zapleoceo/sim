import type { ChannelKind } from '@/lib/types';

/** The messengers of the source filter, in the order the chips are shown (docs/frontend.md). */
export const SOURCE_KINDS: readonly ChannelKind[] = ['telegram_user', 'whatsapp', 'instagram'];

export function isSourceKind(value: unknown): value is ChannelKind {
  return typeof value === 'string' && (SOURCE_KINDS as readonly string[]).includes(value);
}

/**
 * The chosen messengers from the address (`?source=whatsapp,instagram`): known kinds only, each
 * once, in the chip order. A list (an older link) is read too.
 */
export function parseSources(value: unknown): ChannelKind[] {
  const parts = Array.isArray(value) ? value : typeof value === 'string' ? value.split(',') : [];
  const chosen = new Set(parts.map((p) => String(p).trim()).filter(isSourceKind));
  return SOURCE_KINDS.filter((kind) => chosen.has(kind));
}

/** The address value of a choice: `undefined` (no filter) when nothing or everything is chosen. */
export function sourcesParam(kinds: readonly ChannelKind[]): string | undefined {
  const normalized = parseSources([...kinds]);
  return normalized.length === 0 || normalized.length === SOURCE_KINDS.length ? undefined : normalized.join(',');
}

/** One chip pressed: the messenger joins or leaves the choice. */
export function toggleSource(kinds: readonly ChannelKind[], kind: ChannelKind): ChannelKind[] {
  return kinds.includes(kind) ? kinds.filter((k) => k !== kind) : parseSources([...kinds, kind]);
}
