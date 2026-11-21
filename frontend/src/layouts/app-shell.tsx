import { Link, Outlet, useNavigate, useRouterState, type LinkProps } from '@tanstack/react-router';
import { LogOut, MessagesSquare, Monitor, Moon, Smile, Sun, Users, type LucideIcon } from 'lucide-react';
import { Avatar } from '@/components/avatar';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { session, useAdmin } from '@/features/auth/session';
import { useViewportVars } from '@/hooks/use-viewport-vars';
import { useLiveEvents } from '@/lib/events';
import { setTheme, useTheme, type ThemeMode } from '@/lib/theme';
import { cn } from '@/lib/utils';

export interface NavItem { path: NonNullable<LinkProps['to']>; label: string; icon: LucideIcon }

/** The whole dashboard: exactly three sections (docs/frontend.md). */
export const NAV: readonly NavItem[] = [
  { path: '/chats', label: 'Чаты', icon: MessagesSquare },
  { path: '/personas', label: 'Персонажи', icon: Smile },
  { path: '/leads', label: 'Лиды', icon: Users },
];

export function isActivePath(pathname: string, path: string): boolean {
  return pathname === path || pathname.startsWith(`${path}/`);
}

const THEMES: { value: ThemeMode; label: string; icon: LucideIcon }[] = [
  { value: 'light', label: 'Светлая', icon: Sun },
  { value: 'dark', label: 'Тёмная', icon: Moon },
  { value: 'system', label: 'Как в системе', icon: Monitor },
];

function AccountMenu({ compact }: { compact: boolean }) {
  const admin = useAdmin();
  const theme = useTheme();
  const navigate = useNavigate();
  const name = admin?.name || admin?.username || 'Администратор';
  const logout = async (): Promise<void> => {
    await session.logout();
    await navigate({ to: '/login' });
  };
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className={cn(
            'flex items-center gap-2.5 rounded-md p-1.5 text-left transition-colors hover:bg-accent',
            compact ? 'h-12 flex-1 flex-col justify-center gap-0.5 p-0 text-[11px] text-muted-foreground' : 'w-full',
          )}
          aria-label={`${name}: меню`}
        >
          <Avatar name={name} size={compact ? 22 : 30} />
          {compact ? (
            <span>Я</span>
          ) : (
            <span className="flex min-w-0 flex-col leading-tight">
              <span className="truncate text-sm font-medium">{name}</span>
              {admin?.username && <span className="truncate text-xs text-muted-foreground">@{admin.username}</span>}
            </span>
          )}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align={compact ? 'end' : 'start'} className="w-56">
        <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">Тема</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={theme} onValueChange={(v) => setTheme(v as ThemeMode)}>
          {THEMES.map((t) => (
            <DropdownMenuRadioItem key={t.value} value={t.value}>
              <t.icon className="size-4 text-muted-foreground" />
              {t.label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => void logout()}>
          <LogOut />
          Выйти
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/**
 * Responsive shell: a left rail with the three sections on tablets and desktops, a bottom tab bar
 * on phones. Also opens the one live-updates stream for every screen.
 */
export function AppShell() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  useViewportVars();
  useLiveEvents();

  return (
    <div className="flex min-h-dvh">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-background focus:px-3 focus:py-2"
      >
        К содержимому
      </a>
      <aside
        className="sticky top-0 hidden h-dvh w-20 flex-none flex-col items-center border-r bg-sidebar py-3 md:flex xl:w-56 xl:items-stretch xl:px-2"
        aria-label="Основная навигация"
      >
        <Link to="/chats" className="mb-4 flex h-10 items-center gap-2.5 px-2 font-semibold" aria-label="Sim">
          <span className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">S</span>
          <span className="hidden text-[15px] xl:inline">Sim</span>
        </Link>
        <nav aria-label="Разделы" className="flex flex-1 flex-col gap-1">
          {NAV.map((item) => {
            const active = isActivePath(pathname, item.path);
            return (
              <Link
                key={item.path}
                to={item.path}
                aria-current={active ? 'page' : undefined}
                className={cn(
                  'flex flex-col items-center gap-1 rounded-md px-2 py-2 text-[11px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground xl:h-9 xl:flex-row xl:gap-3 xl:px-2.5 xl:py-0 xl:text-sm',
                  active && 'bg-accent font-medium text-accent-foreground',
                )}
              >
                <item.icon className="size-[18px] flex-none" />
                <span className="truncate">{item.label}</span>
              </Link>
            );
          })}
        </nav>
        <div className="w-full border-t px-1 pt-2">
          <AccountMenu compact={false} />
        </div>
      </aside>

      <main id="main" className="flex min-w-0 flex-1 flex-col pb-(--bottom-nav) max-md:overflow-x-clip">
        <Outlet />
      </main>

      <nav
        aria-label="Разделы"
        className="fixed inset-x-0 bottom-0 z-40 flex border-t bg-background/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden kb:hidden"
      >
        {NAV.map((item) => {
          const active = isActivePath(pathname, item.path);
          return (
            <Link
              key={item.path}
              to={item.path}
              aria-current={active ? 'page' : undefined}
              className={cn(
                'flex h-12 min-w-0 flex-1 flex-col items-center justify-center gap-0.5 text-[11px] leading-tight text-muted-foreground',
                active && 'text-primary',
              )}
            >
              <item.icon className="size-5" />
              <span className="max-w-full truncate px-0.5">{item.label}</span>
            </Link>
          );
        })}
        <AccountMenu compact />
      </nav>
    </div>
  );
}
