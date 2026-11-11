import { QueryClient } from '@tanstack/react-query';
import { ApiError } from './api/http';

/** Client errors (4xx) are final; network/5xx get one retry. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failureCount < 1;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { staleTime: 30_000, retry: shouldRetry, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  });
}

/** Query keys in one place, so invalidation never drifts from the reads. */
export const qk = {
  personas: ['personas'] as const,
  strategies: ['strategies'] as const,
  models: ['models'] as const,
  channels: ['channels'] as const,
  content: ['content'] as const,
  admins: ['admins'] as const,
  studio: ['studio'] as const,
  settings: ['settings'] as const,
  dialogs: (filter: unknown) => ['dialogs', filter] as const,
  dialog: (id: number) => ['dialog', id] as const,
  clients: (filter: unknown, page: unknown) => ['clients', filter, page] as const,
  client: (id: number) => ['client', id] as const,
  funnel: (personaId: number | null) => ['funnel', personaId] as const,
  analytics: (from: string, to: string, personaId: number | null) => ['analytics', from, to, personaId] as const,
  activity: (kind: 'messages' | 'llm', args: unknown) => ['activity', kind, args] as const,
  simulations: ['simulations'] as const,
  simulation: (id: number) => ['simulation', id] as const,
};
