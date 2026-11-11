import { useSyncExternalStore } from 'react';

export const PHONE_QUERY = '(max-width: 767px)';
/** Below 1024px the inbox shows one pane: list OR conversation. */
export const SINGLE_PANE_QUERY = '(max-width: 1023px)';
export const WIDE_QUERY = '(min-width: 1280px)';

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

export const useIsPhone = (): boolean => useMediaQuery(PHONE_QUERY);
