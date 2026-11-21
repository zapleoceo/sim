import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter, type AnyRouter,
} from '@tanstack/react-router';
import { render, type RenderResult } from '@testing-library/react';
import type { ReactNode } from 'react';
import { ConfirmProvider } from '@/components/confirm';
import { TooltipProvider } from '@/components/ui/tooltip';
import { createQueryClient } from '@/lib/query';

export interface AppRender extends RenderResult {
  queryClient: QueryClient;
  router: AnyRouter;
}

async function renderRouter(router: AnyRouter): Promise<AppRender> {
  const queryClient = createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false, staleTime: 0 }, mutations: { retry: false } });
  await router.load();
  const result = render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <ConfirmProvider>
          <RouterProvider router={router} />
        </ConfirmProvider>
      </TooltipProvider>
    </QueryClientProvider>,
  );
  return { ...result, queryClient, router };
}

/**
 * Renders one component with the app's providers and a throwaway router (Links and the
 * unsaved-changes guard need one). Queries never retry and are always stale.
 */
export async function renderWithApp(ui: ReactNode): Promise<AppRender> {
  const rootRoute = createRootRoute({ component: () => <>{ui}</> });
  return renderRouter(createRouter({ routeTree: rootRoute, history: createMemoryHistory({ initialEntries: ['/'] }) }));
}

/**
 * Renders a page at its real route id (`/_app/<path>`, so `getRouteApi` works) with the route's
 * own search parser, opened at `url`. `router.state.location.search` shows what the page wrote.
 */
export async function renderAtRoute(
  ui: ReactNode,
  { path, url, validateSearch }: { path: string; url: string; validateSearch: (search: Record<string, unknown>) => object },
): Promise<AppRender> {
  const rootRoute = createRootRoute({ component: Outlet });
  const appRoute = createRoute({ getParentRoute: () => rootRoute, id: '_app', component: Outlet });
  const pageRoute = createRoute({ getParentRoute: () => appRoute, path, validateSearch, component: () => <>{ui}</> });
  const routeTree = rootRoute.addChildren([appRoute.addChildren([pageRoute])]);
  return renderRouter(createRouter({ routeTree, history: createMemoryHistory({ initialEntries: [url] }) }));
}
