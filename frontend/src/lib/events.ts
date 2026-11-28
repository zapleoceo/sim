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
  'generation_failed', 'client_merged',
]);

/** Events that change what the clients table or a client card shows. */
const CLIENT_KINDS: ReadonlySet<string> = new Set<EventKind>([
  'message_in', 'client_updated', 'memory_updated', 'ai_toggled', 'client_merged',
]);

/** Events that may move a planned contact along (planned, its message sent, its call made). */
const CONTACT_KINDS: ReadonlySet<string> = new Set<EventKind>(['initiative', 'message_out', 'send_failed', 'call']);

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
 * What to re-read after an event. The timelines on screen (the conversation shows the client's
 * chats in every messenger), the chat's «Статус Марины» and the client
 * journal on screen («Лента» of a lead card, which joins every chat of the client) always; the chat
 * lists when its row changed; that chat's header when its row or its client changed (the header
 * shows the client's passport); the clients when a client's data changed.
 */
export function invalidationsFor(event: LiveEvent): QueryKey[] {
  // Every timeline: the open conversation joins the client's chats in all messengers, so an event
  // of a sibling chat belongs to it too (only mounted timelines refetch).
  const keys: QueryKey[] = [qk.timelines, qk.chatStatus(event.chat_id), qk.clientTimelines];
  const rowChanged = CHAT_ROW_KINDS.has(event.kind);
  const clientChanged = CLIENT_KINDS.has(event.kind);
  if (rowChanged) keys.push(qk.chatLists);
  if (rowChanged || clientChanged) keys.push(qk.chat(event.chat_id));
  if (clientChanged) keys.push(qk.clients);
  if (event.kind === 'memory_updated') keys.push(qk.chatDays(event.chat_id));
  if (CONTACT_KINDS.has(event.kind)) keys.push(qk.contacts(event.chat_id));
  return keys;
}

/**
 * After a reconnect: the events of the gap are lost (the stream has no ids to resume from), so
 * everything a live event could change is re-read — the chat lists and every chat's header and
 * status, the timelines, the clients and the chats' days. Only what is on screen refetches.
 */
export const RECONNECT_INVALIDATIONS: readonly QueryKey[] = [qk.chats, qk.timelines, qk.clients, qk.allChatDays];

export type EventSourceFactory = (url: string) => Pick<EventSource, 'close'> & {
  onmessage: ((e: MessageEvent<string>) => unknown) | null;
  onopen: ((e: Event) => unknown) | null;
};

const browserSource: EventSourceFactory = (url) => new EventSource(url, { withCredentials: true });

/**
 * Opens the single SSE stream and invalidates the matching queries per event. The browser
 * reconnects by itself after a drop; every open after the first re-reads what the gap may have
 * changed. Returns the cleanup.
 */
export function connectLiveEvents(queryClient: QueryClient, open: EventSourceFactory = browserSource): () => void {
  const source = open(EVENTS_URL);
  const invalidate = (keys: readonly QueryKey[]): void => {
    for (const queryKey of keys) void queryClient.invalidateQueries({ queryKey });
  };
  let opened = false;
  source.onopen = () => {
    if (opened) invalidate(RECONNECT_INVALIDATIONS);
    opened = true;
  };
  source.onmessage = (e) => {
    const event = parseLiveEvent(e.data);
    if (event) invalidate(invalidationsFor(event));
  };
  return () => {
    source.onmessage = null;
    source.onopen = null;
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
