import type { AppSettingItem } from '@/lib/models';

export interface SettingGroup { name: string; items: AppSettingItem[] }

/** Groups in the order the API returns them (the backend owns the order). */
export function groupSettings(items: readonly AppSettingItem[]): SettingGroup[] {
  const groups: SettingGroup[] = [];
  for (const item of items) {
    let group = groups.find((g) => g.name === item.group);
    if (!group) {
      group = { name: item.group, items: [] };
      groups.push(group);
    }
    group.items.push(item);
  }
  return groups;
}

/** Only the given keys, in the given order; no keys means everything as the API returned it. */
export function pickSettings(items: readonly AppSettingItem[], keys?: readonly string[]): AppSettingItem[] {
  if (!keys) return [...items];
  return keys.flatMap((k) => items.filter((i) => i.key === k));
}

export type ParsedSetting = { ok: true; value: string | number } | { ok: false; error: string };

/** Input text -> typed value. Integers are checked against min/max so the API rarely answers 422. */
export function parseSettingValue(item: AppSettingItem, raw: string): ParsedSetting {
  if (item.kind !== 'int') return { ok: true, value: raw };
  const text = raw.trim();
  const value = Number(text);
  if (text === '' || !Number.isInteger(value)) return { ok: false, error: 'Нужно целое число' };
  if (item.min !== null && value < item.min) return { ok: false, error: `Не меньше ${item.min}` };
  if (item.max !== null && value > item.max) return { ok: false, error: `Не больше ${item.max}` };
  return { ok: true, value };
}

/** Hint under a field: help text plus the allowed range for numbers. */
export function settingHint(item: AppSettingItem): string {
  const range = item.min !== null || item.max !== null ? `${item.min ?? '…'}–${item.max ?? '…'}` : '';
  return [item.help, range && `(${range})`].filter(Boolean).join(' ');
}

/** Replace one item after save/reset, keeping the list order. */
export function replaceSetting(items: readonly AppSettingItem[], updated: AppSettingItem): AppSettingItem[] {
  return items.map((i) => (i.key === updated.key ? updated : i));
}

/** React key of a setting's editor: a save or a reset remounts it, so its draft follows the stored value. */
export function settingFieldKey(item: AppSettingItem): string {
  return `${item.key}:${item.updated_at ?? ''}:${String(item.is_default)}`;
}
