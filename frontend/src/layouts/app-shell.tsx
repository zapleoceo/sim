import { Link, Outlet, useNavigate, useRouterState } from '@tanstack/react-router';
import { LogOut, Menu, Monitor, Moon, PanelLeftClose, PanelLeftOpen, Sun } from 'lucide-react';
import { useState } from 'react';
import { Avatar } from '@/components/avatar';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Tip } from '@/components/ui/tooltip';
import { authStore, useAuth } from '@/lib/auth';
import { BOTTOM_COUNT, ROLE_LABELS, visibleNav, type NavItem } from '@/lib/nav';
import { setTheme, useTheme, type ThemeMode } from '@/lib/theme';
import { cn } from '@/lib/utils';

const COLLAPSE_KEY = 'sim-nav-collapsed';

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === '1';
  } catch {
    return false;
  }
}

export function isActivePath(pathname: string, path: string): boolean {
  return pathname === path || pathname.startsWith(`${path}/`);
}

function Brand({ compact }: { compact: boolean }) {
  return (
    <Link to="/" className="flex h-14 items-center gap-2.5 px-3 font-semibold">
      <span className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">S</span>
      {!compact && <span className="text-[15px]">Sim</span>}
    </Link>
  );
}

function NavLinks({ items, compact, pathname, onNavigate }: {
  items: NavItem[];
  compact: boolean;
  pathname: string;
  onNavigate?: () => void;
}) {
  const groups: { key: NavItem['group']; label: string }[] = [
    { key: 'work', label: 'Работа' },
    { key: 'setup', label: 'Настройка' },
  ];
  return (
    <nav aria-label="Разделы" className="flex flex-col gap-4 px-2">
      {groups.map((g) => {
        const list = items.filter((i) => i.group === g.key);
        if (!list.length) return null;
        return (
          <div key={g.key} className="flex flex-col gap-0.5">
            {!compact && <span className="px-2.5 pb-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">{g.label}</span>}
            {list.map((item) => {
              const active = isActivePath(pathname, item.path);
              const Icon = item.icon;
              return (
                <Tip key={item.path} label={compact ? item.label : ''} side="right">
                  <Link
                    to={item.path}
                    onClick={onNavigate}
                    aria-current={active ? 'page' : undefined}
                    aria-label={compact ? item.label : undefined}
                    className={cn(
                      'flex h-9 items-center gap-3 rounded-md px-2.5 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground',
                      active && 'bg-accent font-medium text-accent-foreground',
                      compact && 'justify-center px-0',
                    )}
                  >
                    <Icon className="size-[18px] flex-none" />
                    {!compact && <span className="truncate">{item.label}</span>}
                  </Link>
                </Tip>
              );
            })}
          </div>
        );
      })}
    </nav>
  );
}

const THEMES: { value: ThemeMode; label: string; icon: typeof Sun }[] = [
  { value: 'light', label: 'Светлая', icon: Sun },
  { value: 'dark', label: 'Тёмная', icon: Moon },
  { value: 'system', label: 'Как в системе', icon: Monitor },
];

function UserMenu({ compact }: { compact: boolean }) {
  const { user } = useAuth();
  const theme = useTheme();
  const navigate = useNavigate();
  const name = user?.display_name || user?.tg_username || 'Администратор';
  const role = ROLE_LABELS[user?.role ?? 'operator'];
  const logout = async (): Promise<void> => {
    await authStore.logout();
    await navigate({ to: '/login' });
  };
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className={cn(
            'flex w-full items-center gap-2.5 rounded-md p-1.5 text-left transition-colors hover:bg-accent',
            compact && 'justify-center',
          )}
          aria-label={`${name}, ${role}: меню`}
        >
          <Avatar name={name} url={user?.photo_url} size={30} />
          {!compact && (
            <span className="flex min-w-0 flex-col leading-tight">
              <span className="truncate text-sm font-medium">{name}</span>
              <span className="truncate text-xs text-muted-foreground">{role}</span>
            </span>
          )}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start" className="w-56">
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
 * Responsive shell: sidebar on desktop (collapsible to a rail), bottom tab bar + «Ещё» sheet on phones.
 */
export function AppShell() {
  const { user } = useAuth();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [moreOpen, setMoreOpen] = useState(false);
  const items = visibleNav(user?.role);
  const bottom = items.slice(0, BOTTOM_COUNT);

  const toggle = (): void => {
    setCollapsed((v) => {
      try {
        localStorage.setItem(COLLAPSE_KEY, v ? '0' : '1');
      } catch {
        /* ignore */
      }
      return !v;
    });
  };

  return (
    <div className="flex min-h-dvh">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-background focus:px-3 focus:py-2">
        К содержимому
      </a>
      <aside
        className={cn(
          'sticky top-0 hidden h-dvh flex-none flex-col border-r bg-sidebar transition-[width] duration-200 md:flex',
          collapsed ? 'w-16' : 'w-60',
        )}
        aria-label="Основная навигация"
      >
        <div className={cn('flex items-center justify-between', collapsed && 'justify-center')}>
          {!collapsed && <Brand compact={false} />}
          <Button variant="ghost" size="icon-sm" className={cn(!collapsed && 'mr-2', collapsed && 'mt-3')} onClick={toggle} aria-label={collapsed ? 'Развернуть меню' : 'Свернуть меню'}>
            {collapsed ? <PanelLeftOpen /> : <PanelLeftClose />}
          </Button>
        </div>
        <div className="flex-1 overflow-y-auto py-3 scrollbar-thin">
          <NavLinks items={items} compact={collapsed} pathname={pathname} />
        </div>
        <div className="border-t p-2">
          <UserMenu compact={collapsed} />
        </div>
      </aside>

      <main id="main" className="flex min-w-0 flex-1 flex-col pb-[calc(3.5rem+env(safe-area-inset-bottom))] md:pb-0">
        <Outlet />
      </main>

      <nav
        aria-label="Быстрая навигация"
        className="fixed inset-x-0 bottom-0 z-40 flex border-t bg-background/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden"
      >
        {bottom.map((item) => {
          const active = isActivePath(pathname, item.path);
          const Icon = item.icon;
          return (
            <Link
              key={item.path}
              to={item.path}
              aria-current={active ? 'page' : undefined}
              className={cn('flex h-14 flex-1 flex-col items-center justify-center gap-0.5 text-[11px] text-muted-foreground', active && 'text-primary')}
            >
              <Icon className="size-5" />
              <span className="max-w-full truncate px-1">{item.label}</span>
            </Link>
          );
        })}
        <button
          type="button"
          onClick={() => setMoreOpen(true)}
          aria-haspopup="dialog"
          aria-expanded={moreOpen}
          className="flex h-14 flex-1 flex-col items-center justify-center gap-0.5 text-[11px] text-muted-foreground"
        >
          <Menu className="size-5" />
          <span>Ещё</span>
        </button>
      </nav>

      <Sheet open={moreOpen} onOpenChange={setMoreOpen}>
        <SheetContent side="left" className="w-72 gap-0 p-0">
          <SheetHeader className="p-0">
            <SheetTitle asChild>
              <div><Brand compact={false} /></div>
            </SheetTitle>
            <SheetDescription className="sr-only">Все разделы админки</SheetDescription>
          </SheetHeader>
          <div className="flex-1 overflow-y-auto py-2">
            <NavLinks items={items} compact={false} pathname={pathname} onNavigate={() => setMoreOpen(false)} />
          </div>
          <div className="border-t p-2 pb-[calc(0.5rem+env(safe-area-inset-bottom))]">
            <UserMenu compact={false} />
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
