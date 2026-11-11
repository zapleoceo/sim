import { AppSettingItem } from '../../core/models';

export interface SettingGroup {
  name: string;
  items: AppSettingItem[];
}

/** Группы в порядке, в котором их отдаёт API (порядок задаёт бэкенд). */
export function groupSettings(items: AppSettingItem[]): SettingGroup[] {
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

/** Значение из поля ввода в тип настройки; null, если для числа ввод не число. */
export function parseSettingValue(item: AppSettingItem, raw: string): string | number | null {
  if (item.kind !== 'int') return raw;
  const value = Number(raw.trim());
  return raw.trim() !== '' && Number.isInteger(value) ? value : null;
}
