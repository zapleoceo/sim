import type { QueryClient } from '@tanstack/react-query';
import {
  Outlet, createRootRouteWithContext, createRoute, createRouter, lazyRouteComponent, redirect, type RouterHistory,
} from '@tanstack/react-router';
import { RouteError, RoutePending } from '@/components/route-states';
import { AppShell } from '@/layouts/app-shell';
import { authStore, defaultLanding, hasOwnerRights } from '@/lib/auth';
import { parseActivitySearch, parseInboxSearch, parseLoginSearch } from '@/lib/search';

export interface RouterContext { queryClient: QueryClient }

const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: Outlet,
  errorComponent: RouteError,
});

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/login',
  validateSearch: parseLoginSearch,
  component: lazyRouteComponent(() => import('@/features/login/login-page'), 'LoginPage'),
});

/** Signed-in layout: restores the cookie session once, otherwise sends to /login with returnUrl. */
const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: '_app',
  beforeLoad: async ({ location }) => {
    if (!(await authStore.restore())) {
      throw redirect({ to: '/login', search: { returnUrl: location.href } });
    }
  },
  component: AppShell,
});

const ownerOnly = (): void => {
  if (!hasOwnerRights(authStore.get().user?.role)) throw redirect({ to: '/inbox' });
};
const superadminOnly = (): void => {
  if (authStore.get().user?.role !== 'superadmin') throw redirect({ to: '/inbox' });
};

const indexRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/',
  beforeLoad: () => {
    throw redirect({ to: defaultLanding(authStore.get().user?.role) });
  },
});

const studioRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/studio',
  beforeLoad: ownerOnly,
  component: lazyRouteComponent(() => import('@/features/studio/studio-page'), 'StudioPage'),
});

const inboxRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/inbox',
  validateSearch: parseInboxSearch,
  component: lazyRouteComponent(() => import('@/features/inbox/inbox-page'), 'InboxPage'),
});

const clientsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/clients',
  component: lazyRouteComponent(() => import('@/features/clients/clients-page'), 'ClientsPage'),
});

const clientRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/clients/$id',
  component: lazyRouteComponent(() => import('@/features/clients/client-page'), 'ClientPage'),
});

const activityRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/activity',
  validateSearch: parseActivitySearch,
  component: lazyRouteComponent(() => import('@/features/activity/activity-page'), 'ActivityPage'),
});

const funnelRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/funnel',
  component: lazyRouteComponent(() => import('@/features/funnel/funnel-page'), 'FunnelPage'),
});

const dashboardRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/dashboard',
  component: lazyRouteComponent(() => import('@/features/dashboard/dashboard-page'), 'DashboardPage'),
});

const personasRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/personas',
  beforeLoad: ownerOnly,
  component: lazyRouteComponent(() => import('@/features/personas/personas-page'), 'PersonasPage'),
});

const personaNewRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/personas/new',
  beforeLoad: ownerOnly,
  component: lazyRouteComponent(() => import('@/features/personas/persona-page'), 'PersonaPage'),
});

const personaRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/personas/$id',
  beforeLoad: ownerOnly,
  component: lazyRouteComponent(() => import('@/features/personas/persona-page'), 'PersonaPage'),
});

const settingsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/settings',
  beforeLoad: ownerOnly,
  component: lazyRouteComponent(() => import('@/features/settings/settings-page'), 'SettingsPage'),
});

const guardRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/guard',
  beforeLoad: ownerOnly,
  component: lazyRouteComponent(() => import('@/features/settings/settings-page'), 'GuardPage'),
});

const strategiesRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/strategies',
  beforeLoad: ownerOnly,
  component: lazyRouteComponent(() => import('@/features/strategies/strategies-page'), 'StrategiesPage'),
});

const strategyNewRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/strategies/new',
  beforeLoad: ownerOnly,
  component: lazyRouteComponent(() => import('@/features/strategies/strategy-page'), 'StrategyPage'),
});

const strategyRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/strategies/$id',
  beforeLoad: ownerOnly,
  component: lazyRouteComponent(() => import('@/features/strategies/strategy-page'), 'StrategyPage'),
});

const simulationsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/simulations',
  beforeLoad: ownerOnly,
  component: lazyRouteComponent(() => import('@/features/simulations/simulations-page'), 'SimulationsPage'),
});

const channelsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/channels',
  beforeLoad: ownerOnly,
  component: lazyRouteComponent(() => import('@/features/channels/channels-page'), 'ChannelsPage'),
});

const contentRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/content',
  beforeLoad: ownerOnly,
  component: lazyRouteComponent(() => import('@/features/content/content-page'), 'ContentPage'),
});

const partnerRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/partner',
  beforeLoad: ownerOnly,
  component: lazyRouteComponent(() => import('@/features/partner/partner-page'), 'PartnerPage'),
});

const adminsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/admins',
  beforeLoad: superadminOnly,
  component: lazyRouteComponent(() => import('@/features/admins/admins-page'), 'AdminsPage'),
});

/** Unknown paths go to the landing page (after the auth check). */
const fallbackRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '$',
  beforeLoad: () => {
    throw redirect({ to: '/' });
  },
});

export const routeTree = rootRoute.addChildren([
  loginRoute,
  appRoute.addChildren([
    indexRoute, studioRoute, inboxRoute, clientsRoute, clientRoute, activityRoute, funnelRoute, dashboardRoute,
    personasRoute, personaNewRoute, personaRoute, settingsRoute, guardRoute, strategiesRoute, strategyNewRoute, strategyRoute, simulationsRoute,
    channelsRoute, contentRoute, partnerRoute, adminsRoute, fallbackRoute,
  ]),
]);

/** Page titles by first path segment (document.title). */
export const TITLES: Record<string, string> = {
  login: 'Вход', studio: 'Студия', inbox: 'Диалоги', clients: 'Клиенты', activity: 'Активность', funnel: 'Воронка',
  dashboard: 'Аналитика', personas: 'Персонажи', settings: 'Общие правила', guard: 'Охранник', strategies: 'Стратегии', simulations: 'Симуляции', channels: 'Каналы',
  content: 'Контент', partner: 'Партнёр', admins: 'Администраторы',
};

export function titleFor(pathname: string): string {
  const section = TITLES[pathname.split('/')[1] ?? ''];
  return section ? `${section} — Sim` : 'Sim — админ-панель';
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
