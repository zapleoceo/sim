import type { QueryClient } from '@tanstack/react-query';
import {
  Outlet, createRootRouteWithContext, createRoute, createRouter, lazyRouteComponent, redirect, type RouterHistory,
} from '@tanstack/react-router';
import { RouteError, RoutePending } from '@/components/route-states';
import { session } from '@/features/auth/session';
import { isStage } from '@/features/leads/passport-logic';
import { AppShell } from '@/layouts/app-shell';
import type { LeadStage } from '@/lib/types';
import { positiveInt } from '@/lib/utils';

export interface RouterContext { queryClient: QueryClient }

export interface LoginSearch { returnUrl?: string }
export interface ChatsSearch { id?: number }

/** Only same-app paths are allowed as a post-login target (no open redirect). */
export function safeReturnUrl(value: unknown): string | undefined {
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//') && !value.startsWith('/login')
    ? value
    : undefined;
}

export function parseLoginSearch(s: Record<string, unknown>): LoginSearch {
  const returnUrl = safeReturnUrl(s.returnUrl);
  return returnUrl ? { returnUrl } : {};
}

export function parseChatsSearch(s: Record<string, unknown>): ChatsSearch {
  const id = positiveInt(s.id);
  return id ? { id } : {};
}

/** `/leads?persona=2&stage=hot`: the filters a link may preselect. */
export interface LeadsSearch { persona?: number; stage?: LeadStage }

export function parseLeadsSearch(s: Record<string, unknown>): LeadsSearch {
  const persona = positiveInt(s.persona);
  return { ...(persona ? { persona } : {}), ...(isStage(s.stage) ? { stage: s.stage } : {}) };
}

const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: Outlet,
  errorComponent: RouteError,
});

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/login',
  validateSearch: parseLoginSearch,
  component: lazyRouteComponent(() => import('@/features/auth/login-page'), 'LoginPage'),
});

/** Signed-in layout: restores the cookie session once, otherwise sends to /login with returnUrl. */
const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: '_app',
  beforeLoad: async ({ location }) => {
    if (!(await session.restore())) {
      throw redirect({ to: '/login', search: { returnUrl: location.href } });
    }
  },
  component: AppShell,
});

const indexRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/',
  beforeLoad: () => {
    throw redirect({ to: '/chats' });
  },
});

const chatsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/chats',
  validateSearch: parseChatsSearch,
  component: lazyRouteComponent(() => import('@/features/chats/chats-page'), 'ChatsPage'),
});

const personasRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/personas',
  component: lazyRouteComponent(() => import('@/features/persona/personas-page'), 'PersonasPage'),
});

const personaRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/personas/$personaId',
  component: lazyRouteComponent(() => import('@/features/persona/persona-page'), 'PersonaPage'),
});

const leadsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/leads',
  validateSearch: parseLeadsSearch,
  component: lazyRouteComponent(() => import('@/features/leads/leads-page'), 'LeadsPage'),
});

const leadRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/leads/$clientId',
  component: lazyRouteComponent(() => import('@/features/leads/lead-page'), 'LeadPage'),
});

/** Old addresses of the single-persona dashboard keep working. */
const oldPersonaRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/persona',
  beforeLoad: () => {
    throw redirect({ to: '/personas' });
  },
});

const oldClientsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/clients',
  beforeLoad: () => {
    throw redirect({ to: '/leads', search: {} });
  },
});

const oldClientRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/clients/$clientId',
  beforeLoad: ({ params }) => {
    throw redirect({ to: '/leads/$clientId', params: { clientId: params.clientId } });
  },
});

/** Unknown paths go to the chats (after the auth check). */
const fallbackRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '$',
  beforeLoad: () => {
    throw redirect({ to: '/chats' });
  },
});

export const routeTree = rootRoute.addChildren([
  loginRoute,
  appRoute.addChildren([
    indexRoute, chatsRoute, personasRoute, personaRoute, leadsRoute, leadRoute, oldPersonaRoute, oldClientsRoute,
    oldClientRoute, fallbackRoute,
  ]),
]);

const TITLES: Record<string, string> = { login: 'Вход', chats: 'Чаты', personas: 'Персонажи', leads: 'Лиды' };

export function titleFor(pathname: string): string {
  const section = TITLES[pathname.split('/')[1] ?? ''];
  return section ? `${section} — Sim` : 'Sim';
}

export function createAppRouter(queryClient: QueryClient, history?: RouterHistory) {
  return createRouter({
    routeTree,
    history,
    context: { queryClient },
    defaultPreload: 'intent',
    defaultPendingComponent: RoutePending,
    defaultPendingMs: 250,
    scrollRestoration: true,
  });
}

declare module '@tanstack/react-router' {
  interface Register {
    router: ReturnType<typeof createAppRouter>;
  }
}
