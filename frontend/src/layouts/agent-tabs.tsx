import { Link } from '@tanstack/react-router';
import { useEffect, useRef } from 'react';
import { agentItems, isActivePath, type NavItem } from '@/lib/nav';
import { cn } from '@/lib/utils';

/**
 * Phone only: the agents' sections (Персонажи, Каналы, Симуляции, Контент, Студия) as one strip
 * of pills on top of their pages, behind the single «Агенты» slot of the bottom bar. Scrolls
 * sideways when it does not fit; the current section is kept in view. Desktop has the sidebar.
 */
export function AgentTabs({ items, pathname }: { items: readonly NavItem[]; pathname: string }) {
  const active = useRef<HTMLAnchorElement>(null);
  useEffect(() => {
    active.current?.scrollIntoView?.({ block: 'nearest', inline: 'center' });
  }, [pathname]);

  return (
    <nav
      aria-label="Разделы агентов"
      className="sticky top-0 z-30 border-b bg-background/95 backdrop-blur md:hidden"
    >
      <div className="flex gap-1.5 overflow-x-auto px-3 py-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {agentItems(items).map((item) => {
          const current = isActivePath(pathname, item.path);
          const Icon = item.icon;
          return (
            <Link
              key={item.path}
              to={item.path}
              ref={current ? active : undefined}
              aria-current={current ? 'page' : undefined}
              className={cn(
                'inline-flex h-8 flex-none items-center gap-1.5 rounded-full border px-3 text-sm whitespace-nowrap text-muted-foreground transition-colors',
                current ? 'border-primary bg-primary text-primary-foreground' : 'hover:bg-accent hover:text-foreground',
              )}
            >
              <Icon className="size-4" />
              {item.label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
