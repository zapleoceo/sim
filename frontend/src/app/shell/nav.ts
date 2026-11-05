import { Role } from '../core/models';

export type NavAccess = 'owner' | 'superadmin';
export interface NavItem { path: string; label: string; icon: string; access?: NavAccess }

export const NAV: readonly NavItem[] = [
  { path: '/inbox', label: 'Диалоги', icon: 'forum' },
  { path: '/clients', label: 'Клиенты', icon: 'groups' },
  { path: '/funnel', label: 'Воронка', icon: 'filter_alt' },
  { path: '/dashboard', label: 'Аналитика', icon: 'insights' },
  { path: '/personas', label: 'Персонажи', icon: 'face', access: 'owner' },
  { path: '/strategies', label: 'Стратегии', icon: 'route', access: 'owner' },
  { path: '/simulations', label: 'Симуляции', icon: 'science', access: 'owner' },
  { path: '/channels', label: 'Каналы', icon: 'cable', access: 'owner' },
  { path: '/content', label: 'Контент', icon: 'photo_library', access: 'owner' },
  { path: '/partner', label: 'Партнёр', icon: 'handshake', access: 'owner' },
  { path: '/admins', label: 'Администраторы', icon: 'admin_panel_settings', access: 'superadmin' },
];

export const ROLE_LABELS: Record<Role, string> = {
  superadmin: 'Суперадмин',
  owner: 'Владелец',
  operator: 'Оператор',
};

/** Пункты меню, доступные роли. owner-пункты видят owner и superadmin; «Администраторы» — только superadmin. */
export function visibleNav(role: Role | undefined): NavItem[] {
  return NAV.filter((i) => {
    if (i.access === 'superadmin') return role === 'superadmin';
    if (i.access === 'owner') return role === 'owner' || role === 'superadmin';
    return true;
  });
}
