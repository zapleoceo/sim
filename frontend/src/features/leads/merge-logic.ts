import { channelLook } from '@/components/channel-icon';

/** Who can be merged with whom by hand (docs/leads.md §6). Pure. */

interface Mergeable {
  id: number;
  messengers: readonly string[];
  created_at?: string;
}

/**
 * Why these two leads can not be merged, in Russian; null when they can. A lead has one account
 * per messenger (delivery finds the address by «client + messenger»), as on the server.
 */
export function mergeBlocker(a: Mergeable, b: Mergeable): string | null {
  if (a.id === b.id) return 'Это тот же лид';
  const shared = a.messengers.filter((kind) => b.messengers.includes(kind));
  if (shared.length === 0) return null;
  return `У обоих есть аккаунт в ${shared.map((kind) => channelLook(kind).label).join(', ')} — это разные аккаунты, объединить нельзя`;
}

/** The pair in merge order: the older lead (created first) stays, the other moves into it. */
export function olderFirst<T extends Mergeable>(a: T, b: T): [keep: T, other: T] {
  const older = (a.created_at ?? '') !== (b.created_at ?? '')
    ? (a.created_at ?? '') < (b.created_at ?? '')
    : a.id < b.id;
  return older ? [a, b] : [b, a];
}
