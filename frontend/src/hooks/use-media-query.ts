import { useSyncExternalStore } from 'react';

/** Below 1024 px the chats screen shows one pane at a time: the list OR the conversation. */
export const SINGLE_PANE_QUERY = '(max-width: 1023px)';

export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      if (typeof matchMedia !== 'function') return () => undefined;
      const mq = matchMedia(query);
      mq.addEventListener('change', onChange);
      return () => mq.removeEventListener('change', onChange);
    },
    () => typeof matchMedia === 'function' && matchMedia(query).matches,
    () => false,
  );
}
