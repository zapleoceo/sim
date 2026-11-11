import { createMemoryHistory } from '@tanstack/react-router';
import { render, screen, type RenderResult } from '@testing-library/react';
import { App } from '@/app';
import { authStore } from '@/lib/auth';
import type { Role, User } from '@/lib/models';
import { createQueryClient } from '@/lib/query';
import { createAppRouter } from '@/router';
import { wireHttp } from '@/wiring';

export const CSRF = 'csrf-test-token';

export function testUser(role: Role = 'owner', patch: Partial<User> = {}): User {
  return { id: 1, role, telegram_id: 100, tg_username: 'dima', display_name: 'Дима', photo_url: '', ...patch };
}

/**
 * Renders the whole app (real routes, guards, providers) at `url` on a memory history.
 * The session is pre-applied unless `user` is null; pair with `fakeApi()` for the backend.
 */
export async function renderApp(
  url: string,
  { user = testUser() }: { user?: User | null } = {},
): Promise<RenderResult & { router: ReturnType<typeof createAppRouter> }> {
  if (user) authStore.apply({ user, csrf_token: CSRF });
  const queryClient = createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false, staleTime: 0 }, mutations: { retry: false } });
  const router = createAppRouter(queryClient, createMemoryHistory({ initialEntries: [url] }));
  wireHttp(router, queryClient);
  await router.load();
  const result = render(<App router={router} queryClient={queryClient} />);
  return { ...result, router };
}

export { screen };
