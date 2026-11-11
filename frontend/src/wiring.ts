import type { QueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { configureHttp } from './lib/api/http';
import { authStore } from './lib/auth';
import { errorMessage } from './lib/errors';
import type { createAppRouter } from './router';

/**
 * Connects the HTTP layer to the session, router and toasts:
 * CSRF from the session, 401 -> /login?returnUrl=…, other failures -> one human-readable toast.
 */
export function wireHttp(router: ReturnType<typeof createAppRouter>, queryClient: QueryClient): void {
  configureHttp({
    csrfToken: () => authStore.get().csrf,
    onUnauthorized: () => {
      authStore.clear();
      queryClient.clear();
      const { pathname, href } = router.state.location;
      if (!pathname.startsWith('/login')) {
        void router.navigate({ to: '/login', search: { returnUrl: href } });
      }
    },
    onError: (error) => {
      toast.error(errorMessage(error));
    },
  });
}
