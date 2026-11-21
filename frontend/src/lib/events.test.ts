import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import { EVENTS_URL, connectLiveEvents, invalidationsFor, parseLiveEvent, type EventSourceFactory, type LiveEvent } from './events';
import { qk } from './query';
import type { EventKind } from './types';

const ev = (kind: EventKind, chat_id = 7): LiveEvent => ({ chat_id, event_id: 1, kind });
/** Every event of a chat re-reads its timeline and «Статус Марины». */
const always = (chatId = 7) => [qk.timeline(chatId), qk.chatStatus(chatId)];

describe('invalidationsFor', () => {
  it('a client message refreshes the timeline, the status, the chat lists, that chat and the clients', () => {
    expect(invalidationsFor(ev('message_in'))).toEqual([...always(), qk.chatLists, qk.chat(7), qk.clients]);
  });

  it('an outgoing message or operator flag refreshes the timeline and chat rows, not the clients', () => {
    for (const kind of ['message_out', 'send_failed', 'operator_needed', 'guard_blocked', 'call'] as const) {
      expect(invalidationsFor(ev(kind))).toEqual([...always(), qk.chatLists, qk.chat(7)]);
    }
  });

  it('her decision and a generation change the status in the chat rows', () => {
    for (const kind of ['attention', 'generation_started', 'generation_failed'] as const) {
      expect(invalidationsFor(ev(kind, 3))).toEqual([...always(3), qk.chatLists, qk.chat(3)]);
    }
  });

  it('timeline-only events touch just that chat timeline and status', () => {
    for (const kind of ['media_understood', 'operator_note'] as const) {
      expect(invalidationsFor(ev(kind, 3))).toEqual(always(3));
    }
  });

  it('client data changes refresh the clients but not the chat list', () => {
    expect(invalidationsFor(ev('memory_updated'))).toEqual([...always(), qk.clients, qk.chatDays(7)]);
    expect(invalidationsFor(ev('client_updated'))).toEqual([...always(), qk.clients]);
  });

  it('AI toggle changes both the chat row and the clients', () => {
    expect(invalidationsFor(ev('ai_toggled'))).toEqual([...always(), qk.chatLists, qk.chat(7), qk.clients]);
  });
});

describe('parseLiveEvent', () => {
  it('reads the SSE payload and rejects garbage', () => {
    expect(parseLiveEvent('{"chat_id": 1, "event_id": 42, "kind": "message_in"}')).toEqual({
      chat_id: 1, event_id: 42, kind: 'message_in',
    });
    expect(parseLiveEvent('not json')).toBeNull();
    expect(parseLiveEvent('{"kind": "message_in"}')).toBeNull();
    expect(parseLiveEvent('null')).toBeNull();
  });
});

describe('connectLiveEvents', () => {
  it('opens one stream and invalidates the mapped queries per event', () => {
    const queryClient = new QueryClient();
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries').mockResolvedValue(undefined);
    const source: ReturnType<EventSourceFactory> = { onmessage: null, close: vi.fn() };
    const open = vi.fn<EventSourceFactory>(() => source);

    const stop = connectLiveEvents(queryClient, open);
    expect(open).toHaveBeenCalledWith(EVENTS_URL);

    source.onmessage?.(new MessageEvent('message', { data: '{"chat_id": 4, "event_id": 9, "kind": "operator_note"}' }));
    expect(invalidate).toHaveBeenCalledTimes(2);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: qk.timeline(4) });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: qk.chatStatus(4) });

    source.onmessage?.(new MessageEvent('message', { data: 'broken' }));
    expect(invalidate).toHaveBeenCalledTimes(2);

    stop();
    expect(source.close).toHaveBeenCalled();
    expect(source.onmessage).toBeNull();
  });
});
