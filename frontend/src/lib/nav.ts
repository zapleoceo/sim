import {
  Activity, BarChart3, Bot, Cable, Clapperboard, Filter, FlaskConical, Handshake, Images, MessagesSquare, Route as RouteIcon,
  ShieldCheck, SlidersHorizontal, Smile, Users, Workflow, type LucideIcon,
} from 'lucide-react';
import type { LinkProps } from '@tanstack/react-router';
import type { Role } from './models';

export type NavAccess = 'owner' | 'superadmin';
export type NavGroup = 'work' | 'agents' | 'setup';
export interface NavItem { path: NonNullable<LinkProps['to']>; label: string; icon: LucideIcon; access?: NavAccess; group: NavGroup }

/** Sidebar groups in order. «Агенты»: the personas and everything they live in — their channels,
 * simulations, content and the studio. */
export const NAV_GROUPS: readonly { key: NavGroup; label: string }[] = [
  { key: 'work', label: 'Работа' },
  { key: 'agents', label: 'Агенты' },
  { key: 'setup', label: 'Настройка' },
];

export const NAV: readonly NavItem[] = [
  { path: '/inbox', label: 'Диалоги', icon: MessagesSquare, group: 'work' },
  { path: '/clients', label: 'Клиенты', icon: Users, group: 'work' },
  { path: '/funnel', label: 'Воронка', icon: Filter, group: 'work' },
  { path: '/dashboard', label: 'Аналитика', icon: BarChart3, group: 'work' },
  { path: '/activity', label: 'Активность', icon: Activity, group: 'work' },
  { path: '/personas', label: 'Персонажи', icon: Smile, access: 'owner', group: 'agents' },
  { path: '/channels', label: 'Каналы', icon: Cable, access: 'owner', group: 'agents' },
  { path: '/simulations', label: 'Симуляции', icon: FlaskConical, access: 'owner', group: 'agents' },
  { path: '/content', label: 'Контент', icon: Images, access: 'owner', group: 'agents' },
  { path: '/studio', label: 'Студия', icon: Clapperboard, access: 'owner', group: 'agents' },
  { path: '/settings', label: 'Общие правила', icon: SlidersHorizontal, access: 'owner', group: 'setup' },
  { path: '/agents', label: 'Помощники', icon: Workflow, access: 'owner', group: 'setup' },
  { path: '/strategies', label: 'Стратегии', icon: RouteIcon, access: 'owner', group: 'setup' },
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

export function isActivePath(pathname: string, path: string): boolean {
  return pathname === path || pathname.startsWith(`${path}/`);
}

/** The «Агенты» group of the visible items. */
export function agentItems(items: readonly NavItem[]): NavItem[] {
  return items.filter((i) => i.group === 'agents');
}

/** Is the page one of the agents' sections (the phone shows their switcher on top). */
export function inAgents(items: readonly NavItem[], pathname: string): boolean {
  return agentItems(items).some((i) => isActivePath(pathname, i.path));
}

/** One slot of the phone bottom bar; `match` lists the paths that mark it active. */
export interface BottomSlot { path: NavItem['path']; label: string; icon: LucideIcon; match: readonly string[] }

/** Phone bottom bar: four slots plus «Ещё». With access to the agents: Диалоги, Клиенты, Агенты
 * (all their sections behind one slot) and Аналитика; otherwise the first four work items. */
export function bottomSlots(items: readonly NavItem[]): BottomSlot[] {
  const slot = (i: NavItem): BottomSlot => ({ path: i.path, label: i.label, icon: i.icon, match: [i.path] });
  const work = items.filter((i) => i.group === 'work');
  const agents = agentItems(items);
  if (!agents.length) return work.slice(0, 4).map(slot);
  const pick = (path: string) => work.find((i) => i.path === path);
  const hub: BottomSlot = { path: agents[0].path, label: 'Агенты', icon: Bot, match: agents.map((i) => i.path) };
  return [pick('/inbox'), pick('/clients')].filter((i): i is NavItem => !!i).map(slot)
    .concat(hub, [pick('/dashboard')].filter((i): i is NavItem => !!i).map(slot));
}
