import { AppSettingItem } from '../../core/models';
import { groupSettings, parseSettingValue } from './settings-groups';

const item = (key: string, group: string, kind: AppSettingItem['kind'] = 'text'): AppSettingItem => ({
  key, group, label: key, help: '', kind, min: null, max: null,
  value: '', default: '', is_default: true, updated_at: null, updated_by_id: null,
});

describe('settings groups', () => {
  it('keeps the API order of groups and items', () => {
    const groups = groupSettings([item('a', 'Промпт'), item('b', 'Контролер'), item('c', 'Промпт')]);
    expect(groups.map((g) => g.name)).toEqual(['Промпт', 'Контролер']);
    expect(groups[0].items.map((i) => i.key)).toEqual(['a', 'c']);
  });

  it('parses integers and leaves texts as typed', () => {
    expect(parseSettingValue(item('n', 'g', 'int'), ' 40 ')).toBe(40);
    expect(parseSettingValue(item('n', 'g', 'int'), '4.5')).toBeNull();
    expect(parseSettingValue(item('n', 'g', 'int'), '')).toBeNull();
    expect(parseSettingValue(item('t', 'g'), ' текст ')).toBe(' текст ');
  });
});
