import { useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { qk } from './query';

/** Ids of dialogs that changed, from `event: dialogs` of GET /api/events. */
export function parseDialogsEvent(data: string): number[] {
  try {
    const ids = (JSON.parse(data) as { dialogs?: unknown }).dialogs;
    return Array.isArray(ids) ? ids.filter((x): x is number => Number.isInteger(x)) : [];
  } catch {
    return [];
  }
}

/**
 * Live inbox: the server pushes the ids of changed dialogs (Server-Sent Events, cookie auth,
 * ids only), and the matching queries refresh at once instead of waiting for a poll. The
 * browser reconnects by itself after a drop; the pages keep a slow poll as a fallback.
 */
export function useLiveDialogs(enabled = true): void {
  const queryClient = useQueryClient();
  useEffect(() => {
    if (!enabled || typeof EventSource === 'undefined') return;
    const source = new EventSource('/api/events');
    const onDialogs = (e: MessageEvent<string>): void => {
      const ids = parseDialogsEvent(e.data);
      if (!ids.length) return;
      void queryClient.invalidateQueries({ queryKey: ['dialogs'] });
      void queryClient.invalidateQueries({ queryKey: ['inbox-operator-queue'] });
      for (const id of ids) void queryClient.invalidateQueries({ queryKey: qk.dialog(id) });
    };
    source.addEventListener('dialogs', onDialogs as EventListener);
    return () => {
      source.removeEventListener('dialogs', onDialogs as EventListener);
      source.close();
    };
  }, [enabled, queryClient]);
}
