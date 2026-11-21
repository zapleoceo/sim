import { MutationCache, QueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ApiError, errorMessage } from './api';

/** Client errors (4xx) are final; network and 5xx get one retry. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failureCount < 1;
}

/**
 * Every failed mutation shows one toast with the server's text (`{error}`), so features only
 * add success toasts. A mutation with `meta: { silent: true }` handles its error itself.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    mutationCache: new MutationCache({
      onError: (error, _vars, _ctx, mutation) => {
        if (mutation.options.meta?.silent) return;
        toast.error(errorMessage(error));
      },
    }),
    defaultOptions: {
      queries: { staleTime: 30_000, retry: shouldRetry, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  });
}

/**
 * Query keys in one place, so invalidation never drifts from the reads.
 * Prefix keys (`chats`, `clients`, `media`) invalidate every list and card under them.
 */
export const qk = {
  me: ['auth', 'me'] as const,
  authConfig: ['auth', 'config'] as const,

  chats: ['chats'] as const,
  chatLists: ['chats', 'list'] as const,
  chatList: (q: string) => ['chats', 'list', q] as const,
  chat: (id: number) => ['chats', 'detail', id] as const,
  timeline: (chatId: number) => ['timeline', chatId] as const,
  replyRun: (id: number) => ['reply-run', id] as const,

  persona: ['persona'] as const,
  models: ['models'] as const,
  voices: ['voices'] as const,
  instructions: ['instructions'] as const,
  channels: ['channels'] as const,
  life: ['life', 'world'] as const,
  arcs: ['life', 'arcs'] as const,
  days: ['life', 'days'] as const,
  canon: ['life', 'canon'] as const,
  media: ['media'] as const,
  mediaList: (source: string, kind: string) => ['media', source, kind] as const,

  clients: ['clients'] as const,
  clientList: (q: string) => ['clients', 'list', q] as const,
  client: (id: number) => ['clients', 'detail', id] as const,
};
