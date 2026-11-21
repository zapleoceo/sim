import { useQueryClient, type QueryClient, type QueryKey } from '@tanstack/react-query';
import { useEffect } from 'react';
import { qk } from './query';
import type { EventKind } from './types';

export const EVENTS_URL = '/api/events';

/** One journal event pushed by `GET /api/events` (docs/api.md, «Живые обновления»). */
export interface LiveEvent {
  chat_id: number;
  event_id: number;
  kind: EventKind;
}

/**
 * Events that change a row of the chat list (last message, unread, operator flag, AI, and
 * «Статус Марины»: her phone-check decision, a generation in progress).
 */
const CHAT_ROW_KINDS: ReadonlySet<string> = new Set<EventKind>([
  'message_in', 'message_out', 'message_edited', 'message_deleted', 'send_failed', 'call',
  'guard_blocked', 'operator_needed', 'ai_toggled', 'attention', 'generation_started',
  'generation_failed',
]);

/** Events that change what the clients table or a client card shows. */
const CLIENT_KINDS: ReadonlySet<string> = new Set<EventKind>([
  'message_in', 'client_updated', 'memory_updated', 'ai_toggled',
]);

export function parseLiveEvent(data: string): LiveEvent | null {
  try {
    const v = JSON.parse(data) as Partial<LiveEvent> | null;
    if (!v || !Number.isSafeInteger(v.chat_id) || typeof v.kind !== 'string') return null;
    return { chat_id: v.chat_id as number, event_id: Number(v.event_id) || 0, kind: v.kind };
  } catch {
    return null;
  }
}

/**
 * What to re-read after an event. The chat's own timeline and «Статус Марины» always; the chat
 * lists and that chat's header when its row changed; the clients when a client's data changed.
 */
export function invalidationsFor(event: LiveEvent): QueryKey[] {
  const keys: QueryKey[] = [qk.timeline(event.chat_id), qk.chatStatus(event.chat_id)];
  if (CHAT_ROW_KINDS.has(event.kind)) keys.push(qk.chatLists, qk.chat(event.chat_id));
  if (CLIENT_KINDS.has(event.kind)) keys.push(qk.clients);
  if (event.kind === 'memory_updated') keys.push(qk.chatDays(event.chat_id));
  return keys;
}

export type EventSourceFactory = (url: string) => Pick<EventSource, 'close'> & {
  onmessage: ((e: MessageEvent<string>) => unknown) | null;
};

const browserSource: EventSourceFactory = (url) => new EventSource(url, { withCredentials: true });

/**
 * Opens the single SSE stream and invalidates the matching queries per event. The browser
 * reconnects by itself after a drop. Returns the cleanup.
 */
export function connectLiveEvents(queryClient: QueryClient, open: EventSourceFactory = browserSource): () => void {
  const source = open(EVENTS_URL);
  source.onmessage = (e) => {
    const event = parseLiveEvent(e.data);
    if (!event) return;
    for (const queryKey of invalidationsFor(event)) void queryClient.invalidateQueries({ queryKey });
  };
  return () => {
    source.onmessage = null;
    source.close();
  };
}

/** Mounted once by the app shell: live updates for every screen. */
export function useLiveEvents(): void {
  const queryClient = useQueryClient();
  useEffect(() => {
    if (typeof EventSource === 'undefined') return undefined;
    return connectLiveEvents(queryClient);
  }, [queryClient]);
}
