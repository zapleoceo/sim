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
 * Prefix keys (`chats`, `clients`, `personas`) invalidate every list and card under them.
 */
export const qk = {
  me: ['auth', 'me'] as const,
  authConfig: ['auth', 'config'] as const,

  chats: ['chats'] as const,
  chatLists: ['chats', 'list'] as const,
  /** `personaId` 0 = every persona; `source` = the messengers (`whatsapp,instagram`), '' = all. */
  chatList: (q: string, personaId = 0, source = '') => ['chats', 'list', q, personaId, source] as const,
  /** The counters of the source filter: under `chatLists`, so they refresh with the list. */
  chatSources: (personaId = 0) => ['chats', 'list', '@sources', personaId] as const,
  chat: (id: number) => ['chats', 'detail', id] as const,
  /** «Статус Марины» of one chat (the strip in the conversation header). */
  chatStatus: (id: number) => ['chats', 'status', id] as const,
  /** «Запланировано»: the planned contacts of one chat. */
  contacts: (chatId: number) => ['chats', 'contacts', chatId] as const,
  /** Prefix of every chat's timeline. */
  timelines: ['timeline'] as const,
  timeline: (chatId: number) => ['timeline', chatId] as const,
  replyRun: (id: number) => ['reply-run', id] as const,
  /** Prefix of every chat's days. */
  allChatDays: ['chat-days'] as const,
  /** A chat's day and week summaries (the lead card's «Дни»). */
  chatDays: (chatId: number) => ['chat-days', chatId] as const,

  // Everything of one persona lives under ['personas', id], so `qk.personas` refreshes all.
  personas: ['personas'] as const,
  personaList: ['personas', 'list'] as const,
  persona: (id: number) => ['personas', id] as const,
  channels: (id: number) => ['personas', id, 'channels'] as const,
  /** Her Telegram stickers (installed sets and favourites). */
  stickers: (id: number) => ['personas', id, 'stickers'] as const,
  life: (id: number) => ['personas', id, 'life', 'world'] as const,
  arcs: (id: number) => ['personas', id, 'life', 'arcs'] as const,
  days: (id: number) => ['personas', id, 'life', 'days'] as const,
  canon: (id: number) => ['personas', id, 'life', 'canon'] as const,
  media: (id: number) => ['personas', id, 'media'] as const,
  mediaList: (id: number, source: string, kind: string) => ['personas', id, 'media', source, kind] as const,
  models: ['models'] as const,
  voices: ['voices'] as const,
  agentRegistry: ['agents', 'registry'] as const,
  agents: (id: number) => ['personas', id, 'agents'] as const,

  clients: ['clients'] as const,
  /** `personaId` 0 / `stage` '' = no filter. */
  clientList: (q: string, personaId = 0, stage = '') => ['clients', 'list', q, personaId, stage] as const,
  client: (id: number) => ['clients', 'detail', id] as const,
  /** «Возможно, один человек»: every pair (0) or the pairs of one client. */
  clientMatches: (id = 0) => ['clients', 'matches', id] as const,
  /** Prefix of every client's journal: any chat event may belong to the one on screen. */
  clientTimelines: ['clients', 'timeline'] as const,
  /** The journal of every chat of one client, in one stream. */
  clientTimeline: (id: number) => ['clients', 'timeline', id] as const,
};
