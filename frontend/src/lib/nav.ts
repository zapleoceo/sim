import {
  Activity, BarChart3, Cable, Clapperboard, Filter, FlaskConical, Handshake, Images, MessagesSquare, Route as RouteIcon,
  ShieldCheck, SlidersHorizontal, Smile, Users, type LucideIcon,
} from 'lucide-react';
import type { LinkProps } from '@tanstack/react-router';
import type { Role } from './models';

export type NavAccess = 'owner' | 'superadmin';
export interface NavItem { path: NonNullable<LinkProps['to']>; label: string; icon: LucideIcon; access?: NavAccess; group: 'work' | 'setup' }

export const NAV: readonly NavItem[] = [
  { path: '/studio', label: 'Студия', icon: Clapperboard, access: 'owner', group: 'work' },
  { path: '/inbox', label: 'Диалоги', icon: MessagesSquare, group: 'work' },
  { path: '/clients', label: 'Клиенты', icon: Users, group: 'work' },
  { path: '/funnel', label: 'Воронка', icon: Filter, group: 'work' },
  { path: '/dashboard', label: 'Аналитика', icon: BarChart3, group: 'work' },
  { path: '/activity', label: 'Активность', icon: Activity, group: 'work' },
  { path: '/personas', label: 'Персонажи', icon: Smile, access: 'owner', group: 'setup' },
  { path: '/settings', label: 'Промпты', icon: SlidersHorizontal, access: 'owner', group: 'setup' },
  { path: '/strategies', label: 'Стратегии', icon: RouteIcon, access: 'owner', group: 'setup' },
  { path: '/simulations', label: 'Симуляции', icon: FlaskConical, access: 'owner', group: 'setup' },
  { path: '/channels', label: 'Каналы', icon: Cable, access: 'owner', group: 'setup' },
  { path: '/content', label: 'Контент', icon: Images, access: 'owner', group: 'setup' },
  { path: '/partner', label: 'Партнёр', icon: Handshake, access: 'owner', group: 'setup' },
  { path: '/admins', label: 'Администраторы', icon: ShieldCheck, access: 'superadmin', group: 'setup' },
];

export const ROLE_LABELS: Record<Role, string> = {
  superadmin: 'Суперадмин',
  owner: 'Владелец',
  operator: 'Оператор',
};

/** Items visible to a role: owner items for owner and superadmin; «Администраторы» for superadmin only. */
export function visibleNav(role: Role | undefined): NavItem[] {
  return NAV.filter((i) => {
    if (i.access === 'superadmin') return role === 'superadmin';
    if (i.access === 'owner') return role === 'owner' || role === 'superadmin';
    return true;
  });
}

/** Phone bottom bar: the first work items; the rest live in the «Ещё» sheet. */
export const BOTTOM_COUNT = 4;
