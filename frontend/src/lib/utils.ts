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

/** Positive integer from a route/search param, otherwise null. */
export function positiveInt(value: unknown): number | null {
  const n = typeof value === 'number' ? value : Number(value);
  return Number.isSafeInteger(n) && n > 0 ? n : null;
}

/** «a, b ,, c» -> ['a', 'b', 'c'] (tags typed as one line). */
export function splitTags(value: string): string[] {
  return value
    .split(',')
    .map((t) => t.trim())
    .filter(Boolean);
}

/** Pretty JSON for read-only blocks (plan, request, response). */
export function prettyJson(value: unknown): string {
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2) ?? '';
  } catch {
    return String(value);
  }
}

/** Same JSON = the same value for a form draft (plain data: strings, numbers, arrays, objects). */
export function sameJson(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** Display name of a client: name, else @username, else «Клиент 12». */
export function clientName(c: { id: number; name: string; username: string | null }): string {
  return c.name || (c.username ? `@${c.username}` : `Клиент ${c.id}`);
}
