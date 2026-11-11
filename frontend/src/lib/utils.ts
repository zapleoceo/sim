import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** Initials for an avatar placeholder: «Анна Петрова» -> «АП». */
export function initials(name: string): string {
  return (
    name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((w) => w[0]?.toUpperCase() ?? '')
      .join('') || '?'
  );
}

/** Replace an item by id, or append it when absent (lists after create/update). */
export function upsertById<T extends { id: number }>(list: readonly T[], item: T, prepend = false): T[] {
  if (list.some((x) => x.id === item.id)) return list.map((x) => (x.id === item.id ? item : x));
  return prepend ? [item, ...list] : [...list, item];
}

/** Positive integer from a route/search param, otherwise null. */
export function positiveInt(value: unknown): number | null {
  const n = typeof value === 'number' ? value : Number(value);
  return Number.isSafeInteger(n) && n > 0 ? n : null;
}
