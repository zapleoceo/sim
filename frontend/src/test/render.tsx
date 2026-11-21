import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider, createMemoryHistory, createRootRoute, createRouter } from '@tanstack/react-router';
import { render, type RenderResult } from '@testing-library/react';
import type { ReactNode } from 'react';
import { ConfirmProvider } from '@/components/confirm';
import { TooltipProvider } from '@/components/ui/tooltip';
import { createQueryClient } from '@/lib/query';

/**
 * Renders one component with the app's providers and a throwaway router (Links and the
 * unsaved-changes guard need one). Queries never retry and are always stale.
 */
export async function renderWithApp(ui: ReactNode): Promise<RenderResult & { queryClient: QueryClient }> {
  const queryClient = createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false, staleTime: 0 }, mutations: { retry: false } });
  const rootRoute = createRootRoute({ component: () => <>{ui}</> });
  const router = createRouter({ routeTree: rootRoute, history: createMemoryHistory({ initialEntries: ['/'] }) });
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
  return { ...result, queryClient };
}
