import { visibleNav } from './nav';

describe('visibleNav', () => {
  const labels = (role: Parameters<typeof visibleNav>[0]): string[] => visibleNav(role).map((i) => i.label);

  it('shows Администраторы only to superadmin', () => {
    expect(labels('superadmin')).toContain('Администраторы');
    expect(labels('owner')).not.toContain('Администраторы');
    expect(labels('operator')).not.toContain('Администраторы');
    expect(labels(undefined)).not.toContain('Администраторы');
  });

  it('keeps owner pages for owner and superadmin, hides them from operator', () => {
    expect(labels('owner')).toContain('Персонажи');
    expect(labels('superadmin')).toContain('Персонажи');
    expect(labels('operator')).toEqual(['Диалоги', 'Клиенты', 'Воронка', 'Аналитика']);
  });
});
